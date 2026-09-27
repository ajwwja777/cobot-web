"use strict";
const assert = require('assert').strict;
const { transition, warning, retryDelay } = require('../segmented_frontend/camera_sync_ui.js');

assert.deepEqual(transition(null,{status:'ready',generation:3}), {generation:3,refresh:true,status:'ready'});
assert.deepEqual(transition({generation:3},{status:'ready',generation:3}), {generation:3,refresh:false,status:'ready'});
assert.deepEqual(transition({generation:3},{status:'desynced',generation:3}), {generation:3,refresh:false,status:'desynced'});
assert(/不同步/.test(warning({status:'desynced',skew_ms:187})));
assert(/冻结/.test(warning({status:'frozen',last_advance_age_sec:2.4})));
assert(/过期/.test(warning({status:'stale',stale_keys:['camera_left']})));
assert.equal(warning({status:'ready',skew_ms:40}), '三相机同步 · skew 40 ms');
assert.equal(retryDelay('ready',3), 500);
assert.equal(retryDelay('unavailable',0), 250);
assert.equal(retryDelay('unavailable',5), 2000);
console.log('PASS synchronized camera UI state, warnings, and bounded retry');
