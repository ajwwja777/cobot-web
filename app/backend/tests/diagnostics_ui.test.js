"use strict";
const assert = require("assert").strict;
const {
  formatExplanation, series, actorUpdateNote, MotionBuffer, createPoller, chartModel, latestRun, actorUpdated, legendItems,
} = require("../segmented_frontend/diagnostics_ui.js");

(async () => {
  assert.equal(
    formatExplanation({code:"waiting_for_episodes",pending:1,required:5,missing:4,quarantined:40}).title,
    "还差 4 条即可更新",
  );
  assert(/未发布/.test(formatExplanation({code:"candidate_rejected",episodes:5}).detail));
  assert.deepEqual(
    series([
      {global_step:1,critic_loss:0.2},
      {global_step:2,critic_loss:"bad"},
      {global_step:3,critic_loss:0.1},
    ], "critic_loss"),
    [{x:1,y:0.2},{x:3,y:0.1}],
  );
  assert(/本步未更新 actor/.test(actorUpdateNote({did_actor_update:0,actor_loss:0})));
  assert(/0.1200/.test(actorUpdateNote({did_actor_update:1,actor_loss:0.12})));

  const buffer = new MotionBuffer(3);
  buffer.push(1, {front_right:{velocity:[0,1]}});
  buffer.push(1, {front_right:{velocity:[9,9]}});
  buffer.push(2, {front_right:{velocity:[0,2]}});
  buffer.push(3, {front_right:{velocity:[0,3]}});
  buffer.push(4, {front_right:{velocity:[0,4]}});
  assert.deepEqual(buffer.rows.map(row => row.time), [2,3,4]);
  assert.equal(buffer.rows[2].maxVelocity, 4);

  const model=chartModel([{global_step:100,critic_loss:.3},{global_step:200,critic_loss:.1}], [{key:'critic_loss',label:'Critic',unit:'loss'}], {xLabel:'训练 step',yLabel:'loss'});
  assert.equal(model.x.label,'训练 step');
  assert.equal(model.y.label,'loss');
  assert.equal(model.x.ticks.length,5);
  assert.equal(model.y.ticks.length,5);
  assert.deepEqual(model.series[0].points.map(p=>[p.rawX,p.rawY]),[[100,.3],[200,.1]]);
  assert(/Critic/.test(model.series[0].tooltip(0)));

  let release;
  let calls=0,rendered=0;
  const poller=createPoller(
    () => { calls++; return new Promise(resolve => { release=resolve; }); },
    () => { rendered++; },
    () => {},
  );
  const first=poller.tick();
  assert.equal(await poller.tick(), false);
  assert.equal(calls,1);
  release({ok:true});await first;
  assert.equal(rendered,1);
  // Points are ordered by x and per-series row filters drop zero-filled critic-only rows.
  assert.deepEqual(series([{global_step:3,q:1},{global_step:1,q:2}],"q").map(p=>p.x),[1,3]);
  const mixed=[{global_step:1,actor_q:0,did_actor_update:0},{global_step:2,actor_q:.9,did_actor_update:1}];
  assert.deepEqual(series(mixed,"actor_q","global_step",actorUpdated),[{x:2,y:.9}]);
  const filtered=chartModel(mixed,[{key:'actor_q',when:actorUpdated}],{});
  assert.deepEqual(filtered.series[0].points.map(p=>p.rawY),[.9]);

  // Log axis: decades are evenly spaced, non-positive values are dropped, ticks read back in data units.
  const logModel=chartModel([{global_step:1,l:.001},{global_step:2,l:.1},{global_step:3,l:0}],[{key:'l'}],{logY:true,yLabel:'loss'});
  assert.equal(logModel.series[0].points.length,2);
  assert.equal(logModel.y.label,'loss (log)');
  const [lo,hi]=logModel.series[0].points.map(p=>p.y);
  assert(Math.abs((96-lo)-(96-hi))>60,'two decades should span most of the plot height');
  assert.equal(logModel.y.format(-2),'0.010');

  // Release markers are kept only inside the plotted range.
  const marked=chartModel([{global_step:100,c:1},{global_step:300,c:2}],[{key:'c'}],{markers:[{x:50,label:'old'},{x:200,label:'mid'},{x:300,label:'end'}]});
  assert.deepEqual(marked.markers.map(m=>m.label),['mid','end']);
  assert(marked.markers[0].x>38&&marked.markers[0].x<292);

  // Decision telemetry: only the latest contiguous run of the latest actor.
  const telemetry=[{decision:10,actor_version:7},{decision:20,actor_version:7},{decision:10,actor_version:9},{decision:20,actor_version:9},{decision:30,actor_version:9}];
  assert.deepEqual(latestRun(telemetry).map(r=>[r.decision,r.actor_version]),[[10,9],[20,9],[30,9]]);
  assert.deepEqual(latestRun([{decision:190,actor_version:9},{decision:10,actor_version:9}]).map(r=>r.decision),[10]);
  assert.deepEqual(latestRun([]),[]);
  // Legend entries come from the same definitions as the lines: same order, label and colour.
  assert.deepEqual(legendItems([{key:'autonomous_success',label:'自主成功 (6 条)',color:'#66e0ab'},{key:'x'}]),
    [{label:'自主成功 (6 条)',color:'#66e0ab'},{label:'x',color:'#65d8e8'}]);
  assert.deepEqual(legendItems(undefined),[]);
  console.log("PASS diagnostics explanations, filtering, actor note, motion bounds, and poll guard");
})().catch(error => { console.error(error); process.exitCode=1; });
