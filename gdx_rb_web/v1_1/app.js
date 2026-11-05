'use strict';
const el = id => document.getElementById(id);
const chart = el('chart');
const ctx = chart.getContext('2d');
const app = {
  device:null, sensor:null, deviceStreaming:false, transport:null,
  rows:[], running:false, busy:false, demoTimer:null,
  deviceName:'', sessionMode:null, periodMs:100, startPerfMs:0, lastSamplePerfMs:null,
  renderRequested:false, sessionName:'', exportedRows:0,
  trialId:0, intentionalClose:false,
  classifier:new BreathClassifier(), lastPhase:'NONE', lastSpeechMs:0,
  disconnectedUnexpectedly:false
};
const PHASES = {
  NONE:['待機中','計測開始を待っています','idle'],
  PAUSED:['計測停止中','BLE/USB接続は維持されています。再計測できます。','idle'],
  WARMUP:['判定準備中','張力の変化を約1秒間観測しています','idle'],
  INHALE:['吸っています（推定）','張力が増加しています','inhale'],
  EXHALE:['吐いています（推定）','張力が減少しています','exhale'],
  TRANSITION:['切替・静止中','短い平坦部分は呼吸停止と判定しません','idle'],
  HOLD_CANDIDATE:['呼吸停止の可能性','張力の変化が一定時間小さい状態です。息止めの確定ではありません','hold'],
  NO_SIGNAL:['信号待ち','受信が途絶えています。呼吸停止と判定できません','hold']
};
function status(text, state='') {
  el('statusText').textContent = text;
  el('status').className = 'status'+(state?' '+state:'');
}
function message(text, severity='info') {
  const e=el('message');
  e.textContent=text||'';
  e.className=text?'message active '+severity:'message';
}
function updateButtons() {
  const locked=app.running||app.busy;
  const connected=!!app.device;
  const demoReady=app.transport==='DEMO' && !connected;
  el('connectBtn').disabled=locked||connected;
  el('demoBtn').disabled=locked||connected;
  el('resumeBtn').disabled=locked||!(connected||demoReady);
  el('stopBtn').disabled=!app.running||app.busy;
  el('disconnectBtn').disabled=locked||!(connected||demoReady);
  el('csvBtn').disabled=app.rows.length===0;
  el('clearBtn').disabled=locked||!app.rows.length;
  el('connection').disabled=locked||connected;
  el('sampling').disabled=locked||connected;
  el('resumeBtn').textContent=demoReady?'デモを再計測':'再計測（接続維持）';
  el('disconnectBtn').textContent=demoReady?'デモを終了':'接続解除';
}
function fmt2(v) {return Number.isFinite(v)?v.toFixed(2):'—';}
function localTimestamp() {
  const d=new Date(), p=n=>String(n).padStart(2,'0');
  return `${d.getFullYear()}${p(d.getMonth()+1)}${p(d.getDate())}_${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
}
function settings() {
  return {polarity:el('polarity').value, sensitivity:el('sensitivity').value,
          holdSeconds:Number(el('holdSeconds').value)};
}
function updatePhase(code, details=null) {
  const [label, description, kind]=PHASES[code]||PHASES.NONE;
  el('breathState').textContent=label;
  el('breathExplanation').textContent=description;
  el('phaseCard').className='phase-card '+kind;
  el('phaseSlope').textContent=details&&Number.isFinite(details.slope)?
    `${details.slope>=0?'+':''}${details.slope.toFixed(2)} N/s`:'— N/s';
  if (code!==app.lastPhase) {
    app.lastPhase=code;
    if (app.running && el('voiceFeedback').checked &&
      ['INHALE','EXHALE','HOLD_CANDIDATE'].includes(code)) speakPhase(code);
  }
}
function speakPhase(code) {
  if (!('speechSynthesis' in window)||!('SpeechSynthesisUtterance' in window)) return;
  const now=performance.now();
  if (now-app.lastSpeechMs<1150) return;
  app.lastSpeechMs=now;
  const script=code==='INHALE'?'吸っています':code==='EXHALE'?'吐いています':'張力の変化が小さい状態です';
  try {
    window.speechSynthesis.cancel();
    const u=new SpeechSynthesisUtterance(script);
    u.lang='ja-JP';u.rate=1.06;u.volume=0.75;
    window.speechSynthesis.speak(u);
  } catch (_) {} // Speech is optional; measurement remains active on unsupported systems.
}
function prepareNewSession(mode, name) {
  app.trialId++;
  app.rows=[];
  app.transport=mode;
  app.sessionMode=mode;
  app.deviceName=name;
  app.periodMs=Number(el('sampling').value);
  app.startPerfMs=performance.now();
  app.lastSamplePerfMs=null;
  app.classifier.reset();
  app.lastPhase='NONE';app.lastSpeechMs=0;
  app.sessionName=`GDX_RB_Force_${mode}_${localTimestamp()}_trial${String(app.trialId).padStart(2,'0')}`;
  app.exportedRows=0;
  app.running=true;
  el('forceValue').textContent='—';el('elapsedValue').textContent='—';
  el('countValue').textContent='0';el('observedRate').textContent='—';
  updatePhase('WARMUP');
  updateButtons();scheduleRender();
}
function checkPriorData() {
  if (app.rows.length>app.exportedRows) {
    return confirm('未保存の測定データがあります。新しい計測を始めると表示中のデータがクリアされます。続行しますか？');
  }
  return true;
}
function observedHz() {
  const a=app.rows;if(a.length<2)return null;
  const last=a[a.length-1].pc_elapsed_s;
  let first=a.length-1;
  while(first>0&&a[first-1].pc_elapsed_s>=last-30)first--;
  if(a.length-first<2)return null;
  const dt=last-a[first].pc_elapsed_s;
  return dt>0?(a.length-1-first)/dt:null;
}
function addMeasurement(value, timestampMs=performance.now()) {
  if (!app.running) return;
  const f=Number(value);
  if(!Number.isFinite(f))return;
  app.lastSamplePerfMs=timestampMs;
  const idx=app.rows.length;
  const t=(timestampMs-app.startPerfMs)/1000;
  const s=settings();
  const result=app.classifier.process(t,f,s);
  if(!result)return;
  const r={
    index:idx, utc_received:new Date().toISOString(), pc_elapsed_s:t,
    nominal_elapsed_s:idx*app.periodMs/1000, force_N:f,
    smooth_N:result.smooth, slope_N_per_s:result.slope,
    breath_state:result.code, inhale_when_force:s.polarity,
    sensitivity:s.sensitivity, hold_threshold_s:s.holdSeconds
  };
  app.rows.push(r);
  el('forceValue').textContent=fmt2(f);
  el('elapsedValue').textContent=fmt2(t);
  el('countValue').textContent=String(app.rows.length);
  if(idx%10===0||idx<3) {
    const hz=observedHz();el('observedRate').textContent=hz===null?'—':hz.toFixed(1);
  }
  updatePhase(result.code,result);
  if(idx===0||idx%10===0)updateButtons();
  scheduleRender();
}
function scheduleRender() {
  if(app.renderRequested)return;
  app.renderRequested=true;
  requestAnimationFrame(()=>{app.renderRequested=false;renderChart();});
}
function renderChart() {
  const rect=chart.getBoundingClientRect();
  const w=Math.max(200,rect.width),h=Math.max(190,rect.height);
  const dpr=Math.min(window.devicePixelRatio||1,2);
  if(chart.width!==Math.round(w*dpr)||chart.height!==Math.round(h*dpr)) {
    chart.width=Math.round(w*dpr);chart.height=Math.round(h*dpr);
  }
  ctx.setTransform(dpr,0,0,dpr,0,0);
  ctx.clearRect(0,0,w,h);ctx.fillStyle='#fff';ctx.fillRect(0,0,w,h);
  const margin={left:62,right:21,top:23,bottom:52};
  const plotW=w-margin.left-margin.right,plotH=h-margin.top-margin.bottom;
  const windowS=Number(el('viewWindow').value);
  const lastT=app.rows.length?app.rows[app.rows.length-1].pc_elapsed_s:0;
  const xMin=Math.max(0,lastT-windowS),xMax=xMin+windowS;
  let yMin=0,yMax=50;
  const visible=app.rows.filter(p=>p.pc_elapsed_s>=xMin&&p.pc_elapsed_s<=xMax);
  if(el('yAxis').value==='auto'&&visible.length) {
    let mi=Infinity,ma=-Infinity;
    for(const p of visible){mi=Math.min(mi,p.force_N);ma=Math.max(ma,p.force_N);}
    const span=Math.max(.5,ma-mi);
    yMin=Math.max(0,mi-.2*span);yMax=ma+.2*span;
    if(yMax-yMin<1)yMax=yMin+1;
  }
  const xp=x=>margin.left+(x-xMin)/(xMax-xMin)*plotW;
  const yp=y=>margin.top+(yMax-y)/(yMax-yMin)*plotH;
  ctx.font='12px -apple-system,BlinkMacSystemFont,"Yu Gothic",sans-serif';ctx.lineWidth=1;
  for(let i=0;i<=5;i++){
    const y=margin.top+plotH*i/5,val=yMax-(yMax-yMin)*i/5;
    ctx.strokeStyle='#e5edf2';ctx.beginPath();ctx.moveTo(margin.left,y);ctx.lineTo(margin.left+plotW,y);ctx.stroke();
    ctx.textAlign='right';ctx.textBaseline='middle';ctx.fillStyle='#5d788b';ctx.fillText(val.toFixed(yMax-yMin<=10?1:0),margin.left-12,y);
  }
  for(let i=0;i<=6;i++){
    const x=margin.left+plotW*i/6,val=xMin+windowS*i/6;
    ctx.strokeStyle='#e8eef2';ctx.beginPath();ctx.moveTo(x,margin.top);ctx.lineTo(x,margin.top+plotH);ctx.stroke();
    ctx.textAlign='center';ctx.textBaseline='top';ctx.fillStyle='#617889';ctx.fillText(val.toFixed(0),x,margin.top+plotH+9);
  }
  ctx.strokeStyle='#9db1bd';ctx.beginPath();ctx.moveTo(margin.left,margin.top);ctx.lineTo(margin.left,margin.top+plotH);ctx.lineTo(margin.left+plotW,margin.top+plotH);ctx.stroke();
  ctx.textAlign='center';ctx.fillStyle='#526f80';ctx.fillText('PC受信経過時間 (s)',margin.left+plotW/2,h-12);
  ctx.save();ctx.translate(15,margin.top+plotH/2);ctx.rotate(-Math.PI/2);ctx.textAlign='center';ctx.fillText('Force (N)',0,0);ctx.restore();
  if(!visible.length){
    ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillStyle='#9babba';ctx.font='14px sans-serif';
    ctx.fillText('計測開始後、ここに張力波形を表示します',margin.left+plotW/2,margin.top+plotH/2);return;
  }
  ctx.save();ctx.beginPath();ctx.rect(margin.left,margin.top,plotW,plotH);ctx.clip();
  ctx.beginPath();ctx.strokeStyle='#137f88';ctx.lineWidth=2.2;
  visible.forEach((p,i)=>{const x=xp(p.pc_elapsed_s),y=yp(p.force_N);if(i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y);});
  ctx.stroke();
  if(visible.length===1){ctx.beginPath();ctx.arc(xp(visible[0].pc_elapsed_s),yp(visible[0].force_N),3,0,Math.PI*2);ctx.fillStyle='#137f88';ctx.fill();}
  ctx.restore();
}
function escapeCsvCell(value) {
  const s=String(value??'');return /[",\r\n]/.test(s)?'"'+s.replaceAll('"','""')+'"':s;
}
function downloadCSV() {
  if(!app.rows.length){message('保存できる測定データがありません。','error');return;}
  const header=['sample_index','pc_receive_utc_iso8601','pc_elapsed_s','nominal_elapsed_s',
    'force_N','source','transport','sensor_name','requested_period_ms',
    'smoothed_force_N','force_trend_N_per_s','breath_state','inhale_when_force',
    'sensitivity','hold_threshold_s'];
  const lines=[header.join(',')];
  for(const r of app.rows){
    lines.push([r.index,r.utc_received,r.pc_elapsed_s.toFixed(6),r.nominal_elapsed_s.toFixed(6),r.force_N,
      app.sessionMode==='DEMO'?'DEMO':'GDX_RB',app.sessionMode,app.deviceName,app.periodMs,
      r.smooth_N.toFixed(6),r.slope_N_per_s===null?'':r.slope_N_per_s.toFixed(6),
      r.breath_state,r.inhale_when_force,r.sensitivity,r.hold_threshold_s].map(escapeCsvCell).join(','));
  }
  const blob=new Blob(['\ufeff',lines.join('\r\n'),'\r\n'],{type:'text/csv;charset=utf-8'});
  const uri=URL.createObjectURL(blob), link=document.createElement('a');
  link.href=uri;link.download=app.sessionName+'.csv';document.body.appendChild(link);
  link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(uri),60000);
  app.exportedRows=app.rows.length;
  message(`CSVのダウンロードを開始しました（${app.rows.length}点）。保存先はChromeのダウンロード設定に従います。`);
}
function stopDemoTimer(){if(app.demoTimer!==null){clearInterval(app.demoTimer);app.demoTimer=null;}}
function startDemoTimer(){
  stopDemoTimer();
  const period=app.periodMs;
  app.demoTimer=setInterval(()=>{
    const t=(performance.now()-app.startPerfMs)/1000;
    // Periodically freeze synthetic force to demonstrate the hold-candidate label.
    const cycle=t%16;
    const p=Math.min(cycle,8)*2*Math.PI*.22;
    const v=18+4.5*Math.sin(p-.6)+(cycle<8?.12*Math.sin(3*p):0);
    addMeasurement(v);
  },period);
}
function startDemo(){
  if(app.running||app.busy||app.device||!checkPriorData())return;
  app.transport='DEMO';
  prepareNewSession('DEMO','Synthetic respiration / demo only');
  status('デモ計測中（実センサ未接続）','warn');
  message('模擬波形です。約8秒後から張力変化が小さくなる区間を設けています。実測データとは混在させないでください。');
  startDemoTimer();
}
async function stopAcquisition(){
  if(!app.running||app.busy)return;
  app.running=false;app.busy=true;updateButtons();updatePhase('PAUSED');
  if('speechSynthesis' in window)try{window.speechSynthesis.cancel();}catch(_){}
  stopDemoTimer();
  // Called synchronously in the click handler, maintaining browser user activation.
  if(el('autoSave').checked&&app.rows.length>app.exportedRows)downloadCSV();
  let stopError=null;
  if(app.device&&app.deviceStreaming){
    try{
      if(typeof app.device.stop==='function'){
        await app.device.stop();app.deviceStreaming=false;
      } else {
        // Leave BLE link connected. Ignore values while paused if library has no stop method.
        stopError='このライブラリではstop()が使用できないため、表示とCSVへの記録だけを停止しています。';
      }
    }catch(e){stopError='デバイス側の停止が確認できません: '+String(e&&e.message||e);}
  }
  app.busy=false;
  status(app.device?'計測停止 · 接続維持':`デモ停止 · ${app.rows.length}点`,app.device?'warn':'');
  message(stopError||`記録を停止しました（${app.rows.length}点）。${app.device?'再計測ボタンでセンサを再選択せずに開始できます。':'デモを再計測できます。'}`,stopError?'error':'info');
  updateButtons();scheduleRender();
}
async function startActual(){
  if(app.running||app.busy||app.device||!checkPriorData())return;
  const useBle=el('connection').value==='ble';
  if(!(useBle?navigator.bluetooth:navigator.hid)){
    message(`このブラウザでは${useBle?'Web Bluetooth':'WebHID (USB)'}が利用できません。Google Chromeを使用してください。`,'error');return;
  }
  if(!window.isSecureContext){message('安全な接続コンテキストが必要です。ChromeでHTMLを直接開くかlocalhost/HTTPSを利用してください。','error');return;}
  if(!window.godirect||typeof window.godirect.selectDevice!=='function'){
    message('Vernier公式JavaScriptライブラリを読み込めません。インターネット接続を確認して再読み込みしてください。','error');return;
  }
  app.busy=true;updateButtons();status('デバイス選択中…','warn');
  message('Chromeの選択画面でGDX-RBを選び、接続を許可してください。');
  let dev=null;
  try{
    dev=await window.godirect.selectDevice(useBle);
    const name=String(dev&&dev.name||'');
    if(!/GDX-RB|RESPIRATION BELT/i.test(name))throw new Error(`「${name||'名称不明'}」はGDX-RBではありません。`);
    const sensor=dev.getSensor(1);
    if(!sensor||typeof sensor.setEnabled!=='function')throw new Error('Forceチャンネル1が見つかりません。');
    app.device=dev;app.sensor=sensor;app.transport=useBle?'BLE':'USB';
    app.intentionalClose=false;app.deviceStreaming=false;
    sensor.setEnabled(true);
    // Install the callback once per connection. Repeated starts never duplicate it.
    sensor.on('value-changed',s=>{if(app.device===dev)addMeasurement(s.value);});
    dev.on('device-closed',()=>{
      if(app.device!==dev)return;
      app.device=null;app.sensor=null;app.deviceStreaming=false;
      app.running=false;stopDemoTimer();
      if(!app.intentionalClose){
        app.disconnectedUnexpectedly=true;
        status(`予期せぬ切断 · ${app.rows.length}点`,'warn');
        message('BLE/USB接続が切れました。取得済みのCSVを保存し、再接続してください。','error');
      }
      updatePhase('PAUSED');updateButtons();
    });
    prepareNewSession(app.transport,name);
    await dev.start(app.periodMs);
    if(app.device!==dev)throw new Error('測定開始中にセンサが切断されました。');
    app.deviceStreaming=true;
    status(`実測中 · ${name}`,'running');
    message('計測中です。「計測停止・CSV保存」ではBLEリンクを維持します。再計測を押すと新しいCSVの計測を開始します。');
  }catch(err){
    app.running=false;
    app.device=null;app.sensor=null;app.deviceStreaming=false;
    if(dev){try{await dev.close();}catch(_e){}}
    const detail=err&&err.message?err.message:String(err);
    status('未接続','warn');
    message(/NotFoundError|cancel|No device selected/i.test(detail)?'デバイス選択がキャンセルされました。':'GDX-RBへの接続に失敗しました: '+detail,'error');
    updatePhase('NONE');
  }finally{app.busy=false;updateButtons();scheduleRender();}
}
async function resumeTrial(){
  if(app.running||app.busy||(!app.device&&app.transport!=='DEMO')||!checkPriorData())return;
  app.busy=true;updateButtons();
  const mode=app.device?app.transport:'DEMO';
  const name=app.device?app.deviceName:'Synthetic respiration / demo only';
  try{
    if(app.device && !app.deviceStreaming){
      // Start the device *before* replacing prior rows. If start fails, previous data survives.
      await app.device.start(Number(el('sampling').value));
      if(!app.device)throw new Error('センサとの接続が失われました。');
      app.deviceStreaming=true;
    }
    prepareNewSession(mode,name);
    if(app.device){
      status(`再計測中 · ${name}`,'running');
      message('接続を維持したまま新しい計測を開始しました。前回のデータはCSVとして別ファイルに保存してください。');
    }else{
      status('デモ再計測中','warn');message('デモの新しい測定区間を開始しました。');startDemoTimer();
    }
  }catch(err){
    app.running=false;
    updatePhase('PAUSED');
    status('再計測に失敗 · 接続状態を確認','warn');
    message('再計測を開始できませんでした: '+String(err&&err.message||err)+'。前回の記録は保持しています。「CSVを保存」を使用できます。','error');
  }finally{app.busy=false;updateButtons();scheduleRender();}
}
async function disconnectDevice(){
  if(app.running||app.busy)return;
  if(!app.device){app.transport=null;status('未接続');message('デモを終了しました。');updatePhase('NONE');updateButtons();return;}
  app.busy=true;updateButtons();app.intentionalClose=true;
  const dev=app.device;
  try{
    await dev.close();
    status('接続解除');message('GDX-RBを切断しました。次回はChromeでデバイスを選択してください。');
  }catch(err){
    message('切断処理でエラーが発生しました: '+String(err&&err.message||err),'error');
  }finally{
    app.device=null;app.sensor=null;app.deviceStreaming=false;
    app.busy=false;app.transport=null;app.intentionalClose=false;
    updatePhase('NONE');updateButtons();
  }
}
function clearChart(){
  if(app.running||app.busy||!app.rows.length||!checkPriorData())return;
  app.rows=[];app.exportedRows=0;app.classifier.reset();
  el('forceValue').textContent='—';el('elapsedValue').textContent='—';
  el('countValue').textContent='0';el('observedRate').textContent='—';
  updatePhase(app.device||app.transport==='DEMO'?'PAUSED':'NONE');
  message('グラフをクリアしました。'+(app.device?'BLE/USB接続は維持しています。':''));
  updateButtons();scheduleRender();
}
function resetPhaseOnSettingChange(){
  app.classifier.reset();
  if(app.running){updatePhase('WARMUP');message('呼吸判定設定を変更しました。判定を約1秒間再初期化します。Force生データは変更しません。');}
}
function watchdog(){
  if(!app.running||app.transport==='DEMO')return;
  const t=performance.now();
  const stalled=app.lastSamplePerfMs!==null&&t-app.lastSamplePerfMs>Math.max(3000,app.periodMs*8);
  if(stalled)updatePhase('NO_SIGNAL');
}
function libraryReadyState(ok){
  if(!ok)message('Vernier公式ライブラリを読み込めません。実測にはインターネット接続が必要です。デモは利用可能です。');
}
el('connectBtn').addEventListener('click',startActual);
el('demoBtn').addEventListener('click',startDemo);
el('stopBtn').addEventListener('click',stopAcquisition);
el('resumeBtn').addEventListener('click',resumeTrial);
el('disconnectBtn').addEventListener('click',disconnectDevice);
el('csvBtn').addEventListener('click',downloadCSV);
el('clearBtn').addEventListener('click',clearChart);
el('viewWindow').addEventListener('change',scheduleRender);
el('yAxis').addEventListener('change',scheduleRender);
for(const id of ['polarity','sensitivity','holdSeconds'])el(id).addEventListener('change',resetPhaseOnSettingChange);
el('vernierLibrary').addEventListener('load',()=>libraryReadyState(true));
el('vernierLibrary').addEventListener('error',()=>libraryReadyState(false));
if(typeof ResizeObserver!=='undefined')new ResizeObserver(scheduleRender).observe(chart);
window.addEventListener('resize',scheduleRender);
window.addEventListener('beforeunload',e=>{
  if(app.rows.length>app.exportedRows){e.preventDefault();e.returnValue='';}
});
setInterval(watchdog,600);
updatePhase('NONE');updateButtons();scheduleRender();
