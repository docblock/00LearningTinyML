# Webserver fuer Kamerabilder mit Klassenzuordnung (ESP-VISION, MicroPython)
# Start: diese Datei ausfuehren, oder in main.py: import webserver; webserver.main()
import asyncio
import errno
import gc
import io
import json
import network
import os
import sensor
import time
import vfs

# ---------- Einstellungen ----------
SSID = "Test"
PASSWORD = "testtesttest"
PORT = 80

NUM_CLASSES = 3             # Startwert, auf der Webseite aenderbar
MAX_CLASSES = 50
FRAMESIZE = "VGA"           # Startaufloesung
LIVE_QUALITY = 60           # JPEG-Qualitaet Livebild
SAVE_QUALITY = 90           # JPEG-Qualitaet Download
MAX_LIVE = 3                # gleichzeitige Livebilder
STATE_FILE = "/webserver_state.json"   # Klassenzahl und Zaehler, bleibt nach Neustart erhalten
# -----------------------------------

KNOWN_SIZES = ("QQVGA", "QQCIF", "QCIF", "HQVGA", "QVGA", "CIF", "HVGA",
               "VGA", "SVGA", "XGA", "HD", "SXGA", "UXGA", "FHD")

PAGE = """<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>ESP-VISION Kamera</title>
<style>
body{font-family:sans-serif;margin:0 auto;max-width:760px;padding:12px;background:#f4f4f4;color:#222}
img{width:100%;background:#000;border-radius:6px}
fieldset{margin:12px 0;border:1px solid #bbb;border-radius:6px;background:#fff}
label{margin-right:14px;display:inline-block;padding:4px 0}
button{font-size:1.1em;padding:10px 22px}
#msg{margin-left:10px}
</style></head><body>
<h2>Kamera</h2>
<img id="live" src="/live" alt="Livebild">
<fieldset><legend>Aufl&ouml;sung</legend><select id="res"></select> <span id="dim"></span></fieldset>
<fieldset><legend>Klassen</legend>
Anzahl: <input id="num" type="number" min="1" style="width:5em"> <button id="setnum" style="font-size:1em;padding:3px 10px">&Uuml;bernehmen</button>
<div id="classes"></div></fieldset>
<button id="save">Download</button><span id="msg"></span>
<script>
let st=null,cls=1;const $=i=>document.getElementById(i);
const msg=t=>$('msg').textContent=t;
async function post(u){const r=await fetch(u,{method:'POST'});if(!r.ok)throw new Error(await r.text());return r}
function nextName(c){return 'klasse'+c+'_'+String((st.counts[c]||0)+1).padStart(4,'0')+'.jpg'}
function show(){
 $('num').value=st.classes;$('num').max=st.max;
 $('res').replaceChildren(...st.sizes.map(s=>new Option(s,s,false,s===st.size)));
 $('dim').textContent=st.width+' x '+st.height;
 if(cls>st.classes)cls=st.classes;
 const box=$('classes');box.replaceChildren();
 for(let i=1;i<=st.classes;i++){const l=document.createElement('label'),r=document.createElement('input');
  r.type='radio';r.name='cls';r.value=i;r.checked=i===cls;r.onchange=()=>{cls=i;msg('N\\u00e4chster Name: '+nextName(i))};
  l.append(r,' Klasse '+i+' ('+(st.counts[i]||0)+')');box.append(l)}
}
async function load(){st=await(await fetch('/state')).json();show();msg('N\\u00e4chster Name: '+nextName(cls))}
async function busy(f){for(const b of ['save','setnum','res'])$(b).disabled=true;
 try{await f()}catch(e){msg('Fehler: '+e.message);try{st=await(await fetch('/state')).json();show()}catch(_){}}
 for(const b of ['save','setnum','res'])$(b).disabled=false}
$('res').onchange=()=>busy(async()=>{msg('Stelle um ...');st=await(await post('/resolution?size='+encodeURIComponent($('res').value))).json();show();msg('Aufl\\u00f6sung aktiv: '+st.width+' x '+st.height)});
$('setnum').onclick=()=>busy(async()=>{st=await(await post('/classes?n='+encodeURIComponent($('num').value))).json();show();msg('Klassen: '+st.classes)});
$('save').onclick=()=>busy(async()=>{const r=await post('/save?cls='+cls),name=r.headers.get('X-Filename'),b=await r.blob();
 const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),5000);
 st.counts[cls]=(st.counts[cls]||0)+1;show();msg('Gespeichert: '+name)});
$('live').onerror=()=>setTimeout(()=>{$('live').src='/live?'+Date.now()},2000);
load().catch(e=>msg('Fehler: '+e.message));
</script></body></html>"""


class State:
    def __init__(self):
        self.classes = NUM_CLASSES
        self.counts = {}
        try:
            with open(STATE_FILE) as f:
                d = json.load(f)
            self.classes = min(max(int(d["classes"]), 1), MAX_CLASSES)
            self.counts = {int(k): int(v) for k, v in d["counts"].items()}
        except (OSError, ValueError, KeyError):
            pass

    def save(self):
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"classes": self.classes, "counts": self.counts}, f)
        os.rename(tmp, STATE_FILE)


# img.to_jpeg() kodiert in Software (VGA rund 180 ms), img.save("*.jpg") mit dem
# JPEG-Hardware-Encoder (rund 20 ms). Deshalb in eine "Datei" im RAM speichern.
class RamFile(io.IOBase):
    parts = []

    def write(self, buf):
        RamFile.parts.append(bytes(buf))
        return len(buf)

    def ioctl(self, req, arg):
        return 0


class RamVfs:
    def mount(self, readonly, mkfs):
        pass

    def umount(self):
        pass

    def stat(self, path):
        raise OSError(errno.ENOENT)

    def open(self, path, mode):
        RamFile.parts = []
        return RamFile()


def encode(img, quality):
    img.save("/camram/x.jpg", quality=quality)
    parts, RamFile.parts = RamFile.parts, []
    return parts[0] if len(parts) == 1 else b"".join(parts)


class Camera:
    def __init__(self):
        self.sizes = [n for n in KNOWN_SIZES if hasattr(sensor, n)]
        self.size = None
        self.width = self.height = 0
        self.seq = 0            # Nummer des letzten Livebilds
        self.live_jpg = None
        self.viewers = 0
        self.want_size = None
        self.want_photo = False
        self.photo = None
        self.error = None

    def start(self):
        try:
            vfs.mount(RamVfs(), "/camram")
        except OSError:             # schon eingehaengt
            pass
        sensor.reset()
        sensor.set_pixformat(sensor.RGB565)
        self._apply(FRAMESIZE if FRAMESIZE in self.sizes else self.sizes[-1])

    def _apply(self, name):
        sensor.set_framesize(getattr(sensor, name))
        sensor.skip_frames(n=4)
        self.width, self.height, self.size = sensor.width(), sensor.height(), name

    async def run(self):
        idle = True
        n = 0
        while True:
            try:
                if self.want_size:
                    name, old = self.want_size, self.size
                    try:
                        self._apply(name)
                    except Exception as e:
                        self.error = repr(e)
                        self._apply(old)
                    finally:
                        self.want_size = None
                if not (self.viewers or self.want_photo):
                    idle = True
                    await asyncio.sleep_ms(40)
                    continue
                if idle:                    # alte Bilder im Treiberpuffer verwerfen
                    sensor.skip_frames(n=4)
                    idle = False
                img = sensor.snapshot()
                if self.want_photo:
                    self.photo = encode(img, SAVE_QUALITY)
                    self.want_photo = False
                if self.viewers:
                    self.live_jpg = encode(img, LIVE_QUALITY)
                    self.seq += 1
                n += 1
                if n % 10 == 0:
                    gc.collect()
            except Exception as e:
                print("Kamera:", repr(e))
                await asyncio.sleep_ms(1000)
            await asyncio.sleep_ms(5)      # andere Aufgaben (Netzwerk) bedienen

    async def set_size(self, name):
        self.error = None
        self.want_size = name
        for _ in range(250):
            if self.want_size is None:
                break
            await asyncio.sleep_ms(20)
        else:
            raise OSError("Zeitueberschreitung")
        if self.error:
            raise OSError(self.error)

    async def take(self):
        self.photo = None
        self.want_photo = True
        for _ in range(250):
            if self.photo is not None:
                return self.photo
            await asyncio.sleep_ms(20)
        self.want_photo = False
        raise OSError("kein Kamerabild")


cam = Camera()
state = None
save_lock = None


def state_json():
    return json.dumps({"classes": state.classes, "max": MAX_CLASSES, "counts": state.counts,
                       "sizes": cam.sizes, "size": cam.size, "width": cam.width, "height": cam.height})


async def send(w, status, ctype, body, extra=""):
    if isinstance(body, str):
        body = body.encode()
    w.write(("HTTP/1.1 %s\r\nContent-Type: %s\r\nContent-Length: %d\r\n"
             "Cache-Control: no-store\r\nConnection: close\r\n%s\r\n" % (status, ctype, len(body), extra)).encode())
    w.write(body)
    await asyncio.wait_for(w.drain(), 15)


async def live(w):
    if cam.viewers >= MAX_LIVE:
        return await send(w, "503 Busy", "text/plain", "zu viele Livebilder")
    cam.viewers += 1
    try:
        w.write(b"HTTP/1.1 200 OK\r\nContent-Type: multipart/x-mixed-replace; boundary=frame\r\n"
                b"Cache-Control: no-store\r\nConnection: close\r\n\r\n")
        seq = cam.seq
        while True:
            while cam.seq == seq:
                await asyncio.sleep_ms(5)
            seq, jpg = cam.seq, cam.live_jpg
            w.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(jpg) + jpg + b"\r\n")
            await asyncio.wait_for(w.drain(), 10)
    finally:
        cam.viewers -= 1


async def save(w, query):
    try:
        c = int(query.get("cls", ""))
    except ValueError:
        c = 0
    if not 1 <= c <= state.classes:
        return await send(w, "400 Bad Request", "text/plain", "ungueltige Klasse")
    async with save_lock:
        jpg = await cam.take()
        num = state.counts.get(c, 0) + 1
        name = "klasse%d_%04d.jpg" % (c, num)
        await send(w, "200 OK", "image/jpeg", jpg,
                   "X-Filename: %s\r\nContent-Disposition: attachment; filename=\"%s\"\r\n" % (name, name))
        state.counts[c] = num       # erst zaehlen, wenn das Bild verschickt ist
        state.save()
        print(name, len(jpg), "Byte")


async def handle(r, w):
    try:
        head = b""                      # Kopf blockweise lesen, readline() waere pro Zeile langsam
        while b"\r\n\r\n" not in head:
            part = await asyncio.wait_for(r.read(1024), 10)
            head += part
            if not part or len(head) > 8192:
                return
        line = head.split(b"\r\n", 1)[0].decode().split()
        if len(line) < 2:
            return
        method = line[0]
        path, _, q = line[1].partition("?")
        query = dict(p.split("=", 1) for p in q.split("&") if "=" in p)
        if method == "GET" and path == "/":
            await send(w, "200 OK", "text/html; charset=utf-8", PAGE)
        elif method == "GET" and path == "/live":
            await live(w)
        elif method == "GET" and path == "/state":
            await send(w, "200 OK", "application/json", state_json())
        elif method == "POST" and path == "/save":
            await save(w, query)
        elif method == "POST" and path == "/resolution":
            name = query.get("size", "")
            if name not in cam.sizes:
                await send(w, "400 Bad Request", "text/plain", "unbekannte Aufloesung")
            else:
                try:
                    await cam.set_size(name)
                    print("Aufloesung", cam.width, cam.height)
                    await send(w, "200 OK", "application/json", state_json())
                except OSError as e:
                    await send(w, "500 Error", "text/plain", "Umstellen fehlgeschlagen: %s" % e)
        elif method == "POST" and path == "/classes":
            try:
                n = int(query.get("n", ""))
            except ValueError:
                n = 0
            if 1 <= n <= MAX_CLASSES:
                state.classes = n
                state.save()
                await send(w, "200 OK", "application/json", state_json())
            else:
                await send(w, "400 Bad Request", "text/plain", "Anzahl muss 1 bis %d sein" % MAX_CLASSES)
        else:
            await send(w, "404 Not Found", "text/plain", "nicht gefunden")
    except (OSError, asyncio.TimeoutError):
        pass                        # Verbindung abgebrochen
    except Exception as e:
        print("Anfrage:", repr(e))
    finally:
        try:
            w.close()
            await w.wait_closed()
        except OSError:
            pass


async def serve(port=PORT):
    global state, save_lock
    state = State()
    save_lock = asyncio.Lock()
    cam.start()
    asyncio.create_task(cam.run())
    return await asyncio.start_server(handle, "0.0.0.0", port, backlog=5)


def connect_wifi():
    if SSID == "DEIN_WLAN_NAME":
        raise RuntimeError("Bitte SSID und PASSWORD am Anfang von webserver.py eintragen")
    wlan = network.WLAN(network.STA_IF)
    for attempt in range(1, 4):
        print("WLAN:", SSID, "Versuch", attempt)
        end = time.ticks_add(time.ticks_ms(), 20000)
        asked = False
        while time.ticks_diff(end, time.ticks_ms()) > 0:
            if wlan.isconnected():
                wlan.config(pm=network.WLAN.PM_NONE)
                return wlan
            try:
                if not asked:
                    wlan.active(True)
                if not asked and wlan.status() != network.STAT_CONNECTING:
                    wlan.connect(SSID, PASSWORD)
                    asked = True
            except OSError as e:
                if not e.args or e.args[0] != 16:
                    print("WLAN:", e)
                    time.sleep_ms(500)
                    break
            time.sleep_ms(200)
    raise RuntimeError("WLAN-Verbindung fehlgeschlagen (Name/Passwort pruefen)")


async def watch_wifi(wlan):
    while True:
        await asyncio.sleep(5)
        if not wlan.isconnected():
            print("WLAN getrennt, verbinde neu")
            try:
                wlan.connect(SSID, PASSWORD)
            except OSError as e:
                print("WLAN:", e)


async def amain(wlan):
    await serve()
    print("Webseite: http://%s%s/" % (wlan.ifconfig()[0], "" if PORT == 80 else ":%d" % PORT))
    await watch_wifi(wlan)


def main():
    wlan = connect_wifi()
    try:
        asyncio.run(amain(wlan))
    except KeyboardInterrupt:
        print("gestoppt")
    finally:
        sensor.shutdown()
        asyncio.new_event_loop()
        gc.collect()


if __name__ == "__main__":
    main()

