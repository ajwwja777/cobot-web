const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
test('model choices show path, family, task and distinct training counters in both languages',()=>{
  const root={CobotPreferences:{language:'zh'},addEventListener(){}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../segmented_frontend/collection_model_ui.js'),'utf8'),{window:root});
  const model={checkpoint:'/model/actor_snapshot.pkl',family:'RLT',task:'plug_insertion',
    base_step:4999,stage:'warmup',step:5000,actor_version:2500,available:true};
  const text=root.CobotModelChoiceLabel(model);
  for(const value of ['/model/actor_snapshot.pkl','RLT','plug_insertion','stage1-4999','warmup-5000','actor-2500','可加载']) assert(text.includes(value));
  root.CobotPreferences.language='en';
  const english=root.CobotModelChoiceLabel({...model,available:false,availability:'cli_only'});
  assert(english.includes('CLI available'));
  assert(!/[\u4e00-\u9fff]/u.test(english));
  const dagger=root.CobotModelChoiceLabel({checkpoint:'/model/dagger',family:'π0.5',task:'in_the_pot',step:3000,parent_step:2000,available:true});
  assert(dagger.includes('DAgger 2000+3000'));
});
