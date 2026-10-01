'use strict';
const http = require('http');
const {EmberServer} = require('node-emberplus');
const PORT = Number(process.env.EMBER_INTERNAL_PORT || 9000);
const PUBLIC_IP = process.env.EMBER_PUBLIC_IP || '0.0.0.0';
const PUBLIC_PORT = Number(process.env.EMBER_PUBLIC_PORT || PORT);
const API = `http://127.0.0.1:${process.env.WEB_PORT || 8080}/api/state`;
// Stable field numbers: never reorder existing fields (VSM stores numeric paths).
const FIELDS = [
  ['Left', 'integer', 0], ['Right', 'integer', 0], ['Text', 'integer', 0],
  ['LeftRed', 'boolean', false], ['LeftGreen', 'boolean', false],
  ['RightRed', 'boolean', false], ['RightGreen', 'boolean', false],
  ['TextRed', 'boolean', false], ['TextGreen', 'boolean', false],
  ['Label', 'string', ''], ['Received', 'boolean', false],
  ['Fresh', 'boolean', false], ['SourceIP', 'string', '']
];
function treeJSON(count) {
  return [{number: 0, identifier: 'Tally', description: 'TSL input tally (read only)',
    children: Array.from({length: count}, (_, id) => ({number: id, identifier: `ID_${id}`,
      children: FIELDS.map(([identifier, type, value], number) => ({number, identifier, type, value, access: 'read'}))}))}];
}
function selectInputs(state, config) {
  const selected = new Map();
  const matches = input => input.screen === config.screen && input.id < config.count && input.id >= 0 &&
    (config.source_ip === '*' || input.source_ip === config.source_ip);
  for (const input of state.inputs) {
    if (!matches(input)) continue;
    const old = selected.get(input.id);
    if (!old || input.seen >= old.seen) selected.set(input.id, input);
  }
  // Expose the final routed state as well as raw TSL inputs. Manual tests only
  // exist in outputs, and an output for the same ID is authoritative.
  for (const output of Object.values(state.outputs || {})) {
    const item = output.item || {};
    const routed = {...item,
      seen: output.input_time ?? output.time ?? state.now,
      source_ip: item.source_ip || state.config.output_ip || ''};
    if (matches(routed)) selected.set(routed.id, routed);
  }
  return selected;
}
function values(input, fresh) {
  const {left:l, right:r, text_color:t} = input;
  return [l, r, t, !!(l & 1), !!(l & 2), !!(r & 1), !!(r & 2), !!(t & 1), !!(t & 2),
    input.text, true, fresh, input.source_ip];
}
class Provider {
  constructor() {
    this.server = null;
    this.signature = '';
    this.error = '';
    this.lastOK = 0;
    this.latest = new Map();
  }
  async stop() {
    const server = this.server;
    this.server = null;
    this.signature = '';
    this.latest.clear();
    if (server) {
      for (const client of server.clients) client.socket.destroy();
      await server.close();
    }
  }
  async sync(state) {
    const config = state.config.ember;
    this.lastOK = Date.now();
    if (!config.enabled) { await this.stop(); this.error = ''; return; }
    const signature = JSON.stringify(config);
    if (signature !== this.signature) {
      await this.stop();
      const server = new EmberServer('0.0.0.0', PORT, EmberServer.JSONtoTree(treeJSON(config.count)));
      server.on('error', error => { this.error = String(error.message || error); });
      server.on('clientError', info => { this.error = String(info.error || info); });
      // Drop departed clients from subscriptions even when a value never changes.
      server.on('disconnect', () => {
        for (const set of Object.values(server.subscribers))
          for (const client of set) if (!server.clients.has(client)) set.delete(client);
      });
      try { await server.listen(); }
      catch (error) { await server.close().catch(() => {}); throw error; }
      this.server = server;
      this.signature = signature;
    }
    const selected = selectInputs(state, config);
    for (const [id, input] of selected) this.latest.set(id, input);
    // Hold last state even if the router evicts an old input; Fresh is independent.
    for (const [id, input] of this.latest) {
      const data = values(input, state.now - input.seen <= state.config.stale_seconds);
      data.forEach((value, field) => {
        const path = `0.${id}.${field}`;
        const element = this.server.tree.getElementByPath(path);
        if (element.contents.value === value) return;
        element.contents.value = value;
        this.server.updateSubscribers(path, this.server.getResponse(element));
      });
    }
    this.error = '';
  }
  status() {
    return {available:true, listening:!!this.server, port:PUBLIC_PORT, bind_ip:PUBLIC_IP,
      internal_port:PORT,
      clients:this.server ? this.server.clients.size : 0, error:this.error,
      ids:this.latest.size, lastSync:this.lastOK};
  }
}
async function main() {
  const provider = new Provider();
  const status = http.createServer((req, res) => {
    res.writeHead(200, {'Content-Type':'application/json'});
    res.end(JSON.stringify(provider.status()));
  });
  status.listen(9091, '127.0.0.1');
  let stopping = false;
  const poll = async () => {
    try {
      const response = await fetch(API, {signal:AbortSignal.timeout(1500)});
      if (!response.ok) throw Error(`Router HTTP ${response.status}`);
      await provider.sync(await response.json());
    } catch (error) {
      provider.error = String(error.message || error);
      if (Date.now() - provider.lastOK > 5000) await provider.stop();
    }
    if (!stopping) setTimeout(poll, 100);
  };
  for (const signal of ['SIGTERM', 'SIGINT']) process.on(signal, async () => {
    stopping = true;
    await provider.stop();
    status.close();
    process.exit(0);
  });
  await poll();
}
module.exports = {Provider, treeJSON, selectInputs, values};
if (require.main === module) main().catch(error => { console.error(error); process.exit(1); });
