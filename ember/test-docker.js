// Integration test for the isolated Mac test deployment ONLY.
// Requires no UDP output rules. Injects test ID 10, ends with OFF, restores config.
const assert = require('node:assert/strict');
const dgram = require('node:dgram');
const {EmberClient} = require('emberplus-connection');
const base = 'http://127.0.0.1:8080';
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function api(path, data) {
  const response = await fetch(base + path, data ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)} : {});
  const result = await response.json();
  assert.ok(response.ok, JSON.stringify(result));
  return result;
}
async function main() {
  const original = (await api('/api/state')).config;
  assert.equal(original.rules.length, 0, 'Test must not forward to external targets');
  assert.equal(original.listen_port, 50080);
  const udp = dgram.createSocket('udp4');
  let client;
  const send = color => new Promise((resolve,reject) => {
    const label = Buffer.from('Docker Test');
    const packet = Buffer.alloc(12 + label.length);
    packet.writeUInt16LE(packet.length - 2, 0);
    packet.writeUInt16LE(10, 6);
    packet.writeUInt16LE(192 | color | color<<2 | color<<4, 8);
    packet.writeUInt16LE(label.length, 10); label.copy(packet, 12);
    udp.send(packet,50080,'127.0.0.1', error => error ? reject(error) : resolve());
  });
  try {
    await api('/api/config', {...original, ember:{enabled:true,source_ip:'*',screen:0,count:11}});
    await pause(500);
    assert.equal((await api('/api/ember')).listening, true);
    client = new EmberClient('127.0.0.1', 9000);
    client.on('error', error => console.error('Consumer:', error.message));
    await client.connect();
    await (await client.getDirectory(client.tree)).response;
    let changes = 0;
    const red = await client.getElementByPath('0.10.3', () => changes++);
    const green = await client.getElementByPath('0.10.4');
    const fresh = await client.getElementByPath('0.10.11');
    const received = await client.getElementByPath('0.10.10');
    await send(1); await pause(400);
    assert.equal(red.contents.value, true); assert.equal(green.contents.value, false);
    assert.equal(received.contents.value, true);
    await send(2); await pause(400);
    assert.equal(red.contents.value, false); assert.equal(green.contents.value, true);
    await pause(5500);
    assert.equal(fresh.contents.value, false); assert.equal(green.contents.value, true);
    await send(0); await pause(400);
    assert.equal(green.contents.value, false);
    assert.ok(changes > 0);
    console.log('PASS: Sofie Consumer browsed Docker Provider and received UDP red/green/off changes; stale held color.');
  } finally {
    await send(0).catch(() => {});
    udp.close();
    if (client) {await client.disconnect().catch(() => {}); client.discard();}
    await api('/api/config', original);
  }
}
const timer = setTimeout(() => {console.error('Integration timeout');process.exit(1);},25000);
main().then(()=>{clearTimeout(timer);process.exit(0);}).catch(error=>{console.error(error);process.exit(1);});
