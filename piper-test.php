<?php // UI only (proxy moved to piper-proxy.php) ?>
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Piper TTS + FreeVC Test</title>
    <style>
      :root { color-scheme: light dark; }
      body { font-family: system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, "Apple Color Emoji", "Segoe UI Emoji"; margin: 2rem auto; max-width: 800px; line-height: 1.4; padding: 0 1rem; }
      h1 { font-size: 1.4rem; margin: 0 0 1rem; }
      .row { display: flex; gap: .75rem; align-items: center; flex-wrap: wrap; }
      label { font-weight: 600; }
      input[type="text"], textarea { width: 100%; box-sizing: border-box; font: inherit; padding: .5rem .6rem; }
      textarea { min-height: 120px; }
      button { font: inherit; padding: .5rem .9rem; cursor: pointer; }
      .muted { color: #666; font-size: .9rem; }
      .log { white-space: pre-wrap; background: #00000011; padding: .5rem .6rem; border-radius: .4rem; min-height: 2.2rem; }
      .controls { display: flex; gap: .5rem; align-items: center; }
      .grid { display: grid; gap: .75rem; }
    </style>
  </head>
  <body>
    <h1>Piper TTS + FreeVC Test</h1>

    <p class="muted">
      This page calls <code>piper-proxy.php</code> which proxies to your Piper server via GET so identical requests can be cached by the browser.
      <br />
      1) Start Piper: <code>python3 -m piper.http_server -m de_DE-thorsten-medium</code>
      <br />
      2) Serve this file with PHP, e.g.: <code>php -S 127.0.0.1:8000</code> then open <code>http://127.0.0.1:8000/piper-test.php</code>
    </p>

      <div class="grid">
        <div>
          <label for="target">Piper server URL (target)</label>
          <input id="target" type="text" value="http://127.0.0.1:5000/" />
          <div class="muted">The browser requests <code>piper-proxy.php</code> which forwards to the target.</div>
        </div>

      <div>
        <label for="voice">Voice</label>
        <select id="voice">
          <option value="">Auto (server default / by lang)</option>
        </select>
        <div class="muted">Includes server voices and trained exports in <code>voices-piper/*.onnx</code>.</div>
      </div>

      <div>
        <label for="voice2">Target Voice (voice2)</label>
        <input id="voice2" type="text" placeholder="e.g., bianca (loads voices/bianca.wav)" />
        <div class="muted">If set, server converts TTS output or sound file to the timbre from <code>voices/&lt;voice2&gt;.wav</code>.</div>
      </div>

      <div>
        <label for="sound">Sound (source audio id)</label>
        <input id="sound" type="text" placeholder="e.g., sample1 (streams voices/sample1.wav)" />
        <div class="muted">If set, the server streams <code>voices/&lt;sound&gt;.wav</code> instead of TTS. With voice2, performs voice conversion on that audio.</div>
      </div>

      <div>
        <label for="text">Text</label>
        <textarea id="text" placeholder="Geben Sie hier den zu sprechenden Text ein…">Hallo! Dies ist ein Test von Piper.</textarea>
      </div>

      <div class="controls">
        <button id="btnSpeakStream">Speak</button>
        <button id="btnStop">Stop</button>
        <label style="display:flex; align-items:center; gap:.35rem; font-weight:400;">
          <input id="nocache" type="checkbox" /> No cache
        </label>
        <span id="status" class="muted"></span>
      </div>

      <audio id="player" controls style="display:none"></audio>
      <div class="log" id="log"></div>
    </div>

    <script>
      const $ = (sel) => document.querySelector(sel);
      const player = $("#player");
      const statusEl = $("#status");
      const logEl = $("#log");

      function setStatus(msg) { statusEl.textContent = msg || ""; }
      function log(msg) {
        const t = new Date().toLocaleTimeString();
        logEl.textContent = `[${t}] ${msg}` + (logEl.textContent ? "\n" + logEl.textContent : "");
      }

      async function fetchAsAudio(url, options) {
        const res = await fetch(url, options);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const ct = res.headers.get("content-type") || "";
        if (!ct.includes("audio")) {
          const text = await res.text();
          throw new Error(`Unexpected content-type: ${ct}\nBody: ${text.slice(0, 300)}...`);
        }
        return await res.blob();
      }

      // Voice listing (server + trained)
      async function loadServerVoices(target) {
        try {
          const nocache = document.getElementById('nocache').checked;
          const url = new URL('piper-proxy.php', location.href);
          url.searchParams.set('action','voices');
          if (nocache) { url.searchParams.set('nocache','1'); url.searchParams.set('_', String(Date.now())); }
          const res = await fetch(url.toString(), { cache: nocache ? 'no-store' : 'default' });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          return await res.json();
        } catch (e) {
          log('Server voices error: ' + e);
          return null;
        }
      }
      async function loadTrainedVoices() {
        try {
          const nocache = document.getElementById('nocache').checked;
          const url = new URL('piper-proxy.php', location.href);
          url.searchParams.set('action','trained');
          if (nocache) { url.searchParams.set('nocache','1'); url.searchParams.set('_', String(Date.now())); }
          const res = await fetch(url.toString(), { cache: nocache ? 'no-store' : 'default' });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          return await res.json();
        } catch (e) {
          log('Trained voices error: ' + e);
          return { trained: [] };
        }
      }
      function populateVoices(serverMap, trained) {
        const sel = document.getElementById('voice');
        while (sel.options.length > 1) sel.remove(1);
        if (serverMap && typeof serverMap === 'object') {
          const og = document.createElement('optgroup'); og.label = 'Server Voices';
          const ids = Object.keys(serverMap).sort();
          for (const id of ids) { const o = document.createElement('option'); o.value = id; o.textContent = id; og.appendChild(o); }
          sel.appendChild(og);
        }
        if (trained && Array.isArray(trained.trained)) {
          const og = document.createElement('optgroup'); og.label = 'Trained Voices';
          for (const t of trained.trained) { const label = t.id === 'model' ? `${t.name} (${t.id})` : t.id; const o = document.createElement('option'); o.value = t.id; o.textContent = label; og.appendChild(o); }
          sel.appendChild(og);
        }
      }
      async function refreshVoices() {
        const target = document.getElementById('target').value.trim();
        const [sv, tv] = await Promise.all([loadServerVoices(target), loadTrainedVoices()]);
        let serverMap = null;
        if (sv && typeof sv === 'object') serverMap = sv.voices ? sv.voices : sv;
        populateVoices(serverMap, tv);
      }

      // Download-then-play via PHP proxy
      async function synthesize() {
        const target = $("#target").value.trim();
        const text = $("#text").value.trim();
        const voice = document.getElementById('voice').value;
        const voice2 = document.getElementById('voice2').value.trim();
        const sound = document.getElementById('sound').value.trim();
        if (!text) return alert("Please enter text");
        setStatus("Requesting audio…");
        log(`POST JSON to piper-test.php (target: ${target})`);
        try {
          const blob = await fetchAsAudio("piper-test.php", {
            method: "POST",
            headers: { "content-type": "application/json", "accept": "audio/wav" },
            body: JSON.stringify({ text, target, voice: voice || undefined, voice2: voice2 || undefined, sound: sound || undefined, realtime: true }),
          });
          const url = URL.createObjectURL(blob);
          player.src = url;
          await player.play().catch(() => {});
          setStatus("Playing (downloaded)");
        } catch (err) {
          setStatus("Failed");
          log(`Download error: ${err}`);
          alert("Failed to synthesize speech. See log for details.\n" + String(err));
        }
      }

      // Streaming via WebAudio (parses WAV progressively)
      let audioCtx = null; let workletNode = null; let workletPort = null; let currentStream = null;
      async function ensureAudio() {
        if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        if (!workletNode) {
          const code = `
            class PCMPlayerProcessor extends AudioWorkletProcessor {
              constructor() { super(); this.queue=[]; this.port.onmessage=(e)=>{const m=e.data||{}; if(m.type==='config'){this.channels=m.channels||1;} else if(m.type==='clear'){this.queue=[];} else if(m.type==='data'){const ch=m.channels.map(b=>new Float32Array(b)); this.queue.push({channels:ch,offset:0});}} }
              process(inputs, outputs){const out=outputs[0]; const frames=out[0].length; for(let ch=0; ch<out.length; ch++) out[ch].fill(0); let written=0; while(written<frames && this.queue.length){const it=this.queue[0]; const avail=it.channels[0].length-it.offset; const toCopy=Math.min(frames-written, avail); for(let ch=0; ch<out.length; ch++){const src=it.channels[Math.min(ch,it.channels.length-1)]; out[ch].set(src.subarray(it.offset,it.offset+toCopy), written);} it.offset+=toCopy; written+=toCopy; if(it.offset>=it.channels[0].length) this.queue.shift(); } return true; }
            }
            registerProcessor('pcm-player', PCMPlayerProcessor);
          `;
          const blobUrl = URL.createObjectURL(new Blob([code], { type: 'text/javascript' }));
          await audioCtx.audioWorklet.addModule(blobUrl);
          URL.revokeObjectURL(blobUrl);
          workletNode = new AudioWorkletNode(audioCtx, 'pcm-player', { outputChannelCount: [2] });
          workletPort = workletNode.port; workletNode.connect(audioCtx.destination);
        }
      }
      function parseWavHeader(view){function str(o,l){return String.fromCharCode(...new Uint8Array(view.buffer,o,l));} function u16(o){return new DataView(view.buffer).getUint16(o,true);} function u32(o){return new DataView(view.buffer).getUint32(o,true);} if(str(0,4)!=='RIFF'||str(8,4)!=='WAVE') throw new Error('Not a WAV'); let pos=12, fmt=null, dataPos=null, dataSize=null; while(pos+8<=view.byteLength){const id=str(pos,4); const size=u32(pos+4); const next=pos+8+size; if(id==='fmt '){const audioFormat=u16(pos+8); const numChannels=u16(pos+10); const sampleRate=u32(pos+12); const bitsPerSample=u16(pos+22); fmt={audioFormat,numChannels,sampleRate,bitsPerSample,headerEnd:pos+8+size}; } else if(id==='data'){dataPos=pos+8; dataSize=size; break;} pos=next;} if(!fmt||dataPos==null) throw new Error('Incomplete WAV'); return {fmt,dataPos,dataSize}; }
      function pcm16ToFloat32Interleaved(bytes, channels){const dv=new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength); const sampleCount=dv.byteLength/2; const interleaved=new Float32Array(sampleCount); for(let i=0;i<sampleCount;i++){const s=dv.getInt16(i*2,true); interleaved[i]=Math.max(-1, Math.min(1, s/32768));} const per=[]; const frames=sampleCount/channels; for(let ch=0;ch<channels;ch++) per.push(new Float32Array(frames)); for(let f=0; f<frames; f++){for(let ch=0; ch<channels; ch++){per[ch][f]=interleaved[f*channels+ch];}} return per; }
      function resampleLinear(source, srcRate, dstRate){ if(srcRate===dstRate) return source; const ratio=dstRate/srcRate; return source.map(src=>{const dstFrames=Math.max(1, Math.round(src.length*ratio)); const dst=new Float32Array(dstFrames); for(let i=0;i<dstFrames;i++){const t=i/ratio; const i0=Math.floor(t); const i1=Math.min(src.length-1, i0+1); const frac=t-i0; dst[i]=src[i0]*(1-frac)+src[i1]*frac;} return dst;}); }

      async function synthesizeStream(){
        const target = $("#target").value.trim();
        const text = $("#text").value.trim();
        const voice = document.getElementById('voice').value;
        const voice2 = document.getElementById('voice2').value.trim();
        const sound = document.getElementById('sound').value.trim();
        if (!text && !sound) return alert('Enter text or sound id');
        await ensureAudio(); if (audioCtx.state==='suspended') await audioCtx.resume();
        setStatus('Streaming…'); log(`Stream via piper-proxy.php (target: ${target})`);
        // Abort any prior stream
        if (currentStream && currentStream.controller) { try { currentStream.controller.abort(); } catch {} }
        currentStream = { controller: new AbortController() };
        let res; try {
          const params = new URLSearchParams();
          if (text) params.set('text', text);
          if (voice) params.set('voice', voice);
          if (voice2) params.set('voice2', voice2);
          if (sound) params.set('sound', sound);
          params.set('realtime', 'true');
          const nocache = document.getElementById('nocache').checked;
          if (nocache) { params.set('nocache','1'); params.set('_', String(Date.now())); }
          res = await fetch('piper-proxy.php?' + params.toString(), { method:'GET', headers:{'accept':'audio/*', 'cache-control': nocache ? 'no-cache' : undefined}, cache: nocache ? 'no-store' : 'default', signal: currentStream.controller.signal });
        } catch (e) { log('Fetch failed: '+e); setStatus('Failed'); return; }
        if (!res.ok) { const t = await res.text().catch(()=> ''); log(`HTTP ${res.status}: ${t}`); setStatus('Failed'); return; }
        // If server responded with non-WAV (e.g., MP3 via sound passthrough), download and play via <audio>
        const ctype = (res.headers.get('content-type') || '').toLowerCase();
        if (!ctype.includes('audio/wav')) {
          try {
            const blob = await res.blob();
            const url = URL.createObjectURL(blob);
            // Stop any ongoing stream worklet usage
            try { workletPort && workletPort.postMessage({ type: 'clear' }); } catch {}
            player.src = url; player.style.display = '';
            await player.play().catch(()=>{});
            setStatus('Playing (downloaded)');
            return;
          } catch (err) {
            log('Non-WAV fallback failed: '+err);
            setStatus('Failed');
            return;
          }
        }
        const reader = res.body.getReader();
        currentStream.reader = reader;
        let headerParsed=false, headerBytes=new Uint8Array(0), dataBytes=new Uint8Array(0), fmt=null, srcRate=null, channels=1, bytesPerSample=2, blockAlign=2;
        const ctxRate = audioCtx.sampleRate; workletPort.postMessage({ type:'config', channels:2 });
        function appendBytes(dst, src){ const out=new Uint8Array(dst.length+src.length); out.set(dst,0); out.set(src,dst.length); return out; }
        async function pump(){
          while (true){ const {value, done} = await reader.read(); if (done) break; if (!value) continue; const chunk = new Uint8Array(value);
            if (!headerParsed){ headerBytes = appendBytes(headerBytes, chunk); if (headerBytes.length >= 44){ try { const hdr = parseWavHeader(new DataView(headerBytes.buffer)); fmt=hdr.fmt; if (fmt.audioFormat !== 1) throw new Error('Only PCM'); channels=fmt.numChannels||1; srcRate=fmt.sampleRate; bytesPerSample=(fmt.bitsPerSample||16)/8; blockAlign=channels*bytesPerSample; const after = headerBytes.subarray(hdr.dataPos); dataBytes = appendBytes(dataBytes, after); headerParsed=true; log(`WAV: ${channels}ch @ ${srcRate}Hz`); } catch(e){ continue; } } else { continue; } }
            else { dataBytes = appendBytes(dataBytes, chunk); }
            const complete = dataBytes.length - (dataBytes.length % blockAlign);
            if (complete > 0){ const ready = dataBytes.subarray(0, complete); dataBytes = dataBytes.subarray(complete); const chData = pcm16ToFloat32Interleaved(ready, channels); const rs = resampleLinear(chData, srcRate, ctxRate); let outCh = rs; if (rs.length === 1) { const ch0 = rs[0]; const ch1 = new Float32Array(ch0); outCh = [ch0, ch1]; } workletPort.postMessage({ type:'data', channels: outCh.map(a=>a.buffer) }, outCh.map(a=>a.buffer)); }
          }
          setStatus('Stream ended');
        }
        pump().catch(err => { if (err && err.name==='AbortError'){ log('Stream aborted'); setStatus('Stopped'); return; } log('Stream error: '+err); setStatus('Failed'); });
      }

      // Only streaming button
      $("#btnSpeakStream").addEventListener("click", synthesizeStream);
      function stopAllPlayback(){
        try { player.pause(); player.removeAttribute('src'); player.load(); } catch {}
        if (currentStream && currentStream.controller) { try { currentStream.controller.abort(); } catch {} }
        currentStream = null;
        try { workletPort && workletPort.postMessage({ type: 'clear' }); } catch {}
        if (audioCtx && audioCtx.state==='running') { audioCtx.suspend().catch(()=>{}); }
        setStatus('Stopped');
      }
      $("#btnStop").addEventListener("click", stopAllPlayback);
      (async () => { try { await refreshVoices(); } catch (e) {} })();
      document.getElementById('target').addEventListener('change', () => { refreshVoices(); });
    </script>
  </body>
  </html>
