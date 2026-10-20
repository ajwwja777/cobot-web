"use strict";
const assert=require('node:assert/strict');
const {asyncSummary}=require('../segmented_frontend/unified_collection.js');
assert.equal(asyncSummary({recipe:'native_sync'},1),'');
const o={async_mode:'bounded_async_v1',learner_phase:'updating',pending_upload_episodes:2,pending_episodes:7,async_max_pending:10,published_actor_version:40,downloaded_actor_version:20,served_actor_version:20,policy_generation_lag:1};
const s=asyncSummary(o,1);
for(const text of ['updating','Upload queue 2','Untrained 7/10','Published 40','Downloaded 20','Served 20','Lag 1'])assert.ok(s.includes(text),text);
assert.ok(asyncSummary({...o,network_error:'disconnected'},1).includes('Network retry'));
console.log('PASS async counters distinguish collection, training and served policy');

assert.ok(asyncSummary({...o,collection_waiting_for:'backlog'},1).includes('Wait for learner backlog'));

assert.ok(asyncSummary({...o,async_max_pending:0,pending_episodes:12},1).includes('Untrained 12/unlimited'));


const continuous={async_mode:'native_async_v1',learner_phase:'updating',learner_step:28,critic_updates:560,actor_updates:28,upload:{state:'uploading',files_sent:2,files_total:3,bytes:4096,seconds:1.2,episode_uuid:'episode-18'},download:{state:'downloading',actor_version:27,seconds:2.3,started_at:1700000000},last_episode_at:1700000000,last_replay_insert_at:1700000010,last_update_at:1700000020,published_at:1700000030,timings:{sampling_seconds:0.1,update_seconds:2.5,checkpoint_seconds:0.3,snapshot_seconds:0.4}};
const detail=asyncSummary(continuous,1);
for(const part of ['Continuous learning','GPU updating','Upload uploading 2/3 files','4096 bytes','Downloading Actor 27','Last episode','Replay insertion','Last update','Published at','2023-11-14','Sampling 0.1s','Update 2.5s','Checkpoint 0.3s','Snapshot 0.4s'])assert.ok(detail.includes(part),part);
assert.ok(asyncSummary({...continuous,download:{state:'staged',actor_version:27}},1).includes('Downloaded Actor 27; awaiting local installation'));
assert.ok(asyncSummary({...continuous,network_error:'offline',install_error:'bad checkpoint'},1).includes('Network retry: offline'));
assert.ok(asyncSummary({...continuous,install_error:'bad checkpoint'},1).includes('Policy install failed: bad checkpoint'));
assert.doesNotMatch(asyncSummary({async_mode:'native_async_v1'},1),/1970|NaN|undefined/);
