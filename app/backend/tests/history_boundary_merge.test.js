const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {JSDOM}=require('jsdom');
const source=fs.readFileSync('segmented_frontend/app.js','utf8');
function setup(){
 const dom=new JSDOM('<div id="timeline"></div><div id="episode-endpoints"></div><div id="node-preview"></div><div id="replay-panel"></div><video id="video"></video><input id="slider"><div id="markers"></div><span id="clock"></span>',{url:'http://localhost',runScripts:'outside-only'});
 const w=dom.window;
 Object.assign(w,{$:s=>w.document.querySelector(s),historyIsRlt:()=>true,historyEndpoint:p=>'/api/rlt-recorder/api'+p,cachedFrameSource:p=>p});
 w.eval(source.slice(source.indexOf('function rltHistoryNodes('),source.indexOf('function renderConsole(')));
 w.eval(source.slice(source.indexOf('function showNode('),source.indexOf('function render(status)')));
 w.eval(fs.readFileSync('segmented_frontend/replay_timeline.js','utf8'));
 return {dom,w};
}
function kinds(nodes){return Array.from(nodes,n=>n.endpoint||n.node_kind);}
function render(w,labels,frames){
 const nodes=w.rltHistoryNodes(labels,frames);
 w.viewedEpisode={episode_uuid:'paused-history',frame_count:frames,nodes};
 w.showEpisodeEndpoints(w.viewedEpisode);w.renderTimeline();
 const timeline=w.CobotReplayTimeline.create(w.document.querySelector('#video'),{slider:w.document.querySelector('#slider'),markers:w.document.querySelector('#markers'),label:w.document.querySelector('#clock')},{onNode:w.showNode});
 timeline.setEpisode(nodes,{source_fps:30,source_frame_count:frames,duration:frames/30});
 return nodes;
}
test('paused episode 22 ends with exactly start/end across buttons, cards and replay markers',()=>{
 const {dom,w}=setup();try{
  const labels={operator_nodes:[{frame_index:92,node_kind:'pause'}]},before=JSON.stringify(labels);
  const nodes=render(w,labels,93);
  assert.deepEqual(kinds(nodes),['start','end']);
  assert.equal(w.document.querySelectorAll('.node-button').length,2);
  assert.equal(w.document.querySelectorAll('.replay-marker').length,2);
  for(const button of w.document.querySelectorAll('.node-button'))button.click();
  assert.equal(w.document.querySelector('#episode-endpoints').children.length,2);
  assert.equal(w.document.querySelector('#episode-endpoints').children[1].dataset.nodeKey,'end');
  assert.match(w.document.querySelector('#timeline').textContent,/\u7ed3\u675f.*92/);
  assert.doesNotMatch(w.document.querySelector('#timeline').textContent,/\u6682\u505c/);
  assert.equal(nodes[1].merged_operator_nodes[0].node_kind,'pause');
  assert.equal(JSON.stringify(labels),before);
 }finally{dom.window.close();}
});
test('same-frame pause/resume collapses to one selectable middle boundary',()=>{
 const {dom,w}=setup();try{
  const labels={operator_nodes:[{frame_index:20,node_kind:'pause'},{frame_index:20,node_kind:'resume'}]};
  const nodes=render(w,labels,93);
  assert.equal(nodes.length,3);assert.equal(nodes[1].frame_index,20);
  assert.match(nodes[1].display_label,/\u6682\u505c\/\u7ee7\u7eed/);
  assert.equal(nodes[1].merged_operator_nodes.length,2);
  assert.equal(w.document.querySelectorAll('.replay-marker').length,3);
  w.document.querySelectorAll('.node-button')[1].click();
  w.document.querySelectorAll('.node-button')[1].click();
  const cards=w.document.querySelector('#episode-endpoints');
  assert.equal(cards.children.length,3);
  assert.equal(cards.children[1].dataset.frameIndex,'20');
  assert.match(cards.children[1].textContent,/\u6682\u505c\/\u7ee7\u7eed/);
 }finally{dom.window.close();}
});
test('different frames, manual markers and interior HIL boundaries stay visible',()=>{
 const {dom,w}=setup();try{
  const nodes=w.rltHistoryNodes({operator_nodes:[{frame_index:20,node_kind:'pause'},{frame_index:21,node_kind:'resume'},{frame_index:30,node_kind:'marker'}],intervention_phases:[{start_frame:40,end_frame:50,human_sides:['right']}]},93);
  assert.deepEqual(kinds(nodes),['start','pause','resume','marker','hil','end']);
  assert.deepEqual(Array.from(nodes,n=>n.frame_index),[0,20,21,30,40,92]);
  assert.match(nodes[4].display_label,/HIL.*40.*50/);
 }finally{dom.window.close();}
});
test('repeated boundaries at endpoints merge without collapsing distinct start/end in one-frame episodes',()=>{
 const {dom,w}=setup();try{
  const nodes=w.rltHistoryNodes({operator_nodes:[{frame_index:0,node_kind:'pause'},{frame_index:0,node_kind:'resume'},{frame_index:92,node_kind:'pause'},{frame_index:92,node_kind:'pause'}]},93);
  assert.deepEqual(kinds(nodes),['start','end']);
  assert.equal(nodes[0].merged_operator_nodes.length,2);assert.equal(nodes[1].merged_operator_nodes.length,2);
  assert.deepEqual(kinds(w.rltHistoryNodes({operator_nodes:[{frame_index:0,node_kind:'pause'}]},1)),['start','end']);
 }finally{dom.window.close();}
});
