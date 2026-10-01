'use strict';
process.env.EMBER_INTERNAL_PORT = '19000';
const assert = require('node:assert/strict');
const {Provider, values, selectInputs} = require('./provider');
const {EmberClient} = require('node-emberplus');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const timeout = setTimeout(() => { console.error('Test timed out'); process.exit(1); }, 15000);
async function main() {
  const provider = new Provider();
  let client = new EmberClient('127.0.0.1', 19000);
  client.on('error', error => { console.error('Consumer error:', error.message); });
  const state = {now:100, config:{stale_seconds:5,output_ip:'10.207.20.19',
    ember:{enabled:true,source_ip:'*',screen:0,count:11}}, inputs:[], outputs:{}};
  const input = {id:1,screen:0,left:1,right:2,text_color:3,text:'CAM 1',source_ip:'192.0.2.1',seen:100};
  assert.deepEqual(values(input, true).slice(0,9), [1,2,3,true,false,false,true,true,true]);
  assert.equal(selectInputs({...state,inputs:[input]}, {...state.config.ember,source_ip:'192.0.2.2'}).size, 0);
  try {
    await provider.sync(state);
    provider.server.on('error', error => console.error('Provider error:', error));
    await client.connect();
    await client.getDirectory();
    const red = await client.getElementByPath('0.1.3');
    console.log('Tree read OK');
    assert.equal(red.contents.value, false);
    let updates = 0;
    await client.getDirectory(red, () => updates++);
    const fresh = await client.getElementByPath('0.1.11');
    state.inputs = [input];
    await provider.sync(state);
    await pause(150);
    assert.equal(red.contents.value, true);
    assert.equal(fresh.contents.value, true);
    assert.ok(updates > 0, 'Subscribed update arrived');
    state.now = 120;
    await provider.sync(state);
    await pause(150);
    assert.equal(red.contents.value, true, 'Stale retains red');
    assert.equal(fresh.contents.value, false);
    console.log('Subscription and hold OK');
    state.outputs.manual = {item:{id:1,screen:0,left:2,right:1,text_color:1,text:'MANUAL'},
      time:121,input_time:121,mode:'manual'};
    state.now = 121;
    await provider.sync(state);
    await pause(150);
    const green = await client.getElementByPath('0.1.4');
    assert.equal(red.contents.value, false, 'Manual output overrides raw input');
    assert.equal(green.contents.value, true, 'Manual output reaches Ember+');
    assert.equal(provider.latest.get(1).source_ip, '10.207.20.19');
    state.outputs = {};
    console.log('Manual routed output OK');
    await client.setValue(red, false);
    assert.equal(provider.server.tree.getElementByPath('0.1.4').contents.value, true, 'Read-only rejects writes');
    state.inputs = [{...input,left:0,seen:120}];
    await provider.sync(state);
    await pause(150);
    assert.equal(red.contents.value, false, 'Explicit off updates subscriber');
    await client.disconnect();
    console.log('Read-only and off OK; reconnecting');
    client = new EmberClient('127.0.0.1', 19000);
    client.on('error', error => console.error(error.message));
    await client.connect();
    await client.getDirectory();
    const current = await client.getElementByPath('0.1.0');
    assert.equal(current.contents.value, 0, 'Reconnect sees current value');
    state.config.ember.enabled = false;
    await provider.sync(state);
    assert.equal(provider.status().listening, false);
    console.log('PASS: tree, filter, booleans, subscription, stale hold, explicit off, read-only, reconnect, disable');
  } finally {
    await client.disconnect().catch(() => {});
    await provider.stop();
    clearTimeout(timeout);
  }
}
main().then(() => process.exit(0)).catch(error => {console.error(error); process.exit(1);});
