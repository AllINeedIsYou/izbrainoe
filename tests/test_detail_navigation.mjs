import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const source=readFileSync(new URL('../izbrainoe/static/app.js',import.meta.url),'utf8');
const part=source.slice(source.indexOf('async function openItem('),source.indexOf('async function loadPreview('));
const pending=[];let renders=0;
const scope={state:{navigation:0,page:'home',group:'',q:'',detail:null},api:path=>new Promise(resolve=>pending.push({path,resolve})),render:()=>renders++,window:{scrollTo(){}}};
vm.createContext(scope);vm.runInContext(part,scope);
// Slow response from A must not replace B, which the user opened afterwards.
const a=scope.openItem('A');const b=scope.openItem('B');
pending[1].resolve({id:'B'});await b;pending[0].resolve({id:'A'});await a;
assert.equal(scope.state.detail.id,'B');assert.equal(renders,1);
// Reanalysis completing after navigation must not touch null state.detail.
scope.state.page='home';scope.state.detail=null;
assert.equal(scope.applyDetail({id:'B'},'B',{page:'home'}),false);
assert.equal(scope.state.detail,null);
// Reanalysis for B cannot overwrite C.
scope.state.page='detail';scope.state.detail={id:'C'};
assert.equal(scope.applyDetail({id:'B'},'B',{page:'home'}),false);
assert.equal(scope.state.detail.id,'C');
// Current record still updates and preserves its return destination.
assert.equal(scope.applyDetail({id:'C',title:'Updated'},'C',{page:'collections'}),true);
assert.equal(scope.state.detail.from.page,'collections');
console.log('Навигация: запоздалые ответы не перезаписывают другую карточку и не обращаются к null.');
