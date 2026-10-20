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
