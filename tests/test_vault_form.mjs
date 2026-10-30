import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {webcrypto} from 'node:crypto';

const source=readFileSync(new URL('../izbrainoe/static/app.js',import.meta.url),'utf8');
const start=source.indexOf("document.addEventListener('submit'");
const end=source.indexOf("document.addEventListener('input'",start);
assert.ok(start>=0&&end>start);

async function submit(formId,values,existing=[]){
  const saved=[],errors=[];
  let handler;
  const button={disabled:false,textContent:'Сохранить'};
  // HTMLFormElement exposes named controls before prototype properties.
  // In the real vault form, <input name="id"> makes form.id an input element.
  const form={id:{value:values.id||''},getAttribute:name=>name==='id'?formId:null,
    querySelector:selector=>selector==='[type=submit]'?button:{textContent:''}};
  const scope={
    document:{addEventListener:(event,callback)=>{assert.equal(event,'submit');handler=callback}},
    FormData:class{get(name){return values[name]??null}has(name){return name in values}},
    crypto:webcrypto,unlocked:{entries:existing},
    saveVault:async entries=>saved.push(entries),closeModal:()=>{},render:()=>{},
    toast:()=>{},error:message=>errors.push(message)
  };
  vm.runInNewContext(source.slice(start,end),scope);
  await handler({target:form,preventDefault(){}});
  assert.deepEqual(errors,[]);
  assert.equal(button.disabled,false);
  return saved;
}

const created=await submit('vault-entry-form',{id:'',title:'Тестовый аккаунт',login:'fake@example.invalid',password:'TEST_ONLY',aliases:'тест',notes:''});
assert.equal(created.length,1);
assert.equal(created[0][0].title,'Тестовый аккаунт');
assert.equal(created[0][0].password,'TEST_ONLY');
const updated=await submit('vault-entry-form',{id:'one',title:'Изменённый тест',login:'fake',password:'TEST_ONLY'},[{id:'one',title:'Старое'}]);
assert.equal(updated[0].length,1);
assert.equal(updated[0][0].title,'Изменённый тест');
const deleted=await submit('vault-delete-form',{id:'one'},[{id:'one',title:'Тест'}]);
assert.equal(deleted.length,1);
assert.equal(deleted[0].length,0);
console.log('Формы паролей: создание, изменение и удаление работают при наличии скрытого поля id.');
