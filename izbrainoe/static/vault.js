// Secrets and the non-extractable key live only in memory. No localStorage.
const enc=new TextEncoder(), dec=new TextDecoder();
export const b64=bytes=>btoa(String.fromCharCode(...new Uint8Array(bytes)));
export const unb64=text=>Uint8Array.from(atob(text),c=>c.charCodeAt(0));
export async function derive(password,salt){
  const material=await crypto.subtle.importKey('raw',enc.encode(password),'PBKDF2',false,['deriveKey']);
  return crypto.subtle.deriveKey({name:'PBKDF2',salt,iterations:600000,hash:'SHA-256'},material,{name:'AES-GCM',length:256},false,['encrypt','decrypt']);
}
export async function encryptVault(key,salt,entries){
  const iv=crypto.getRandomValues(new Uint8Array(12));
  const plain=enc.encode(JSON.stringify({check:'izbrainoe-v1',entries}));
  const encrypted=await crypto.subtle.encrypt({name:'AES-GCM',iv,additionalData:enc.encode('izbrainoe-vault-v1')},key,plain);
  plain.fill(0);
  return {v:1,kdf:'PBKDF2-SHA256',iterations:600000,salt:b64(salt),iv:b64(iv),ciphertext:b64(encrypted)};
}
export async function decryptVault(password,envelope){
  if(envelope.v!==1||envelope.iterations!==600000||envelope.kdf!=='PBKDF2-SHA256')throw Error('Неподдерживаемый формат хранилища.');
  const salt=unb64(envelope.salt), key=await derive(password,salt);
  const plain=await crypto.subtle.decrypt({name:'AES-GCM',iv:unb64(envelope.iv),additionalData:enc.encode('izbrainoe-vault-v1')},key,unb64(envelope.ciphertext));
  const data=JSON.parse(dec.decode(plain));new Uint8Array(plain).fill(0);
  if(data.check!=='izbrainoe-v1'||!Array.isArray(data.entries))throw Error('Повреждено хранилище.');
  return {key,salt,entries:data.entries};
}

export async function wrapBotKey(password,saltText,challenge,version){
  if(challenge.version!==version)throw Error('Хранилище изменилось. Заблокируй и открой его заново.');
  const material=await crypto.subtle.importKey('raw',enc.encode(password),'PBKDF2',false,['deriveBits']);
  const raw=new Uint8Array(await crypto.subtle.deriveBits({name:'PBKDF2',salt:unb64(saltText),iterations:600000,hash:'SHA-256'},material,256));
  let plain;
  try{
    const publicKey=await crypto.subtle.importKey('spki',unb64(challenge.spki),{name:'RSA-OAEP',hash:'SHA-256'},false,['encrypt']);
    plain=enc.encode(JSON.stringify({key:b64(raw),salt:saltText,nonce:challenge.nonce,version}));
    const wrapped=await crypto.subtle.encrypt({name:'RSA-OAEP'},publicKey,plain);
    return {wrapped_key:b64(wrapped),nonce:challenge.nonce};
  }finally{raw.fill(0);plain?.fill(0)}
}

export async function lookupTokens(entries){
  return Promise.all(entries.map(async entry=>{
    const words=[...new Set((String(entry.title||'')+' '+String(entry.aliases||'')).toLowerCase().replace(/ё/g,'е').match(/[\p{L}\p{N}_]+/gu)||[])].filter(w=>w.length>1).slice(0,64);
    return Promise.all(words.map(async word=>{
      const digest=new Uint8Array(await crypto.subtle.digest('SHA-256',enc.encode(word)));
      return [...digest].map(b=>b.toString(16).padStart(2,'0')).join('');
    }));
  }));
}
