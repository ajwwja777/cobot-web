const test = (name, fn) => fn().then(() => console.log('PASS '+name)).catch(e => {console.error(e); process.exitCode=1;});
const assert = require('assert').strict;
const fs = require('fs');
const vm = require('vm');
test('late labels cannot overwrite a newer episode selection', async () => {
 const src=fs.readFileSync('frontend/app.js','utf8');
 const load=src.slice(src.indexOf('async function loadEpisode('),src.indexOf('async function refreshEpisodes('));
 const nodes=new Map();
 const $=key=>{if(!nodes.has(key))nodes.set(key,{value:null,textContent:'',classList:{add(){},remove(){},toggle(){}}});return nodes.get(key)};
 const pending={}; let phases;
 const context={state:{selectedEpisodeUuid:null,episodeGeneration:0},$,resetReplay(){},updateButtons(){},queryRoot(){return ''},selectedEpisode(){return null},episodeFilename(){return ''},formatBytes(){return ''},renderInterventions(p){phases=p},request(url){return new Promise(resolve=>{pending[url.split('/')[3]]=resolve})}};
 vm.createContext(context);vm.runInContext(load+';this.loadEpisode=loadEpisode;',context);
 const a=context.loadEpisode('A');const b=context.loadEpisode('B');
 pending.B({intervention_phases:['B'],episode_outcome:'success'});await b;
 pending.A({intervention_phases:['A'],episode_outcome:'failure'});await a;
 assert.deepEqual(phases,['B']);assert.equal(context.state.selectedEpisodeUuid,'B');
});
