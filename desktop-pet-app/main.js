const { app, BrowserWindow, screen, ipcMain, Menu } = require('electron');
const fs = require('fs');
const os = require('os');
const path = require('path');

const cfg = JSON.parse(fs.readFileSync(path.join(__dirname, 'config.json'), 'utf8'));

const SIZE = 150;
const rand = (a, b) => a + Math.random() * (b - a);
const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];

let win;
let personality;
let pos = { x: 300, y: 300 };
let target = null;
let state = 'idle';
let dir = 1;
let stateUntil = 0;
let fullness = 0; // 0-100，被吃的文件越大越饱
let bubble = '';
let lastSent = '';

// ---------- 性格：第一次启动随机生成，之后保持不变 ----------
function loadPersonality() {
  const file = path.join(app.getPath('userData'), 'personality.json');
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch (e) {
    /* 第一次启动，往下生成 */
  }
  const p = {
    name: pick(['团子', '糯米', '阿墨', '小炸毛', '豆包', '布丁']),
    temper: Math.random(), // 脾气：越高越容易发火
    appetite: Math.random(), // 食欲：越高吃得越香、越容易撑
    energy: Math.random(), // 精力：越高越爱跑
  };
  fs.writeFileSync(file, JSON.stringify(p, null, 2));
  return p;
}

// ---------- 状态机：随机决定下一步做什么 ----------
function setState(s, ms, b = '') {
  state = s;
  stateUntil = Date.now() + ms;
  bubble = b;
}

function chooseNext(wa) {
  if (fullness >= 85) {
    setState('stuffed', 6000, '😵');
    fullness = 70; // 躺平一会儿，缓过来
    return;
  }
  const e = personality.energy;
  const r = Math.random();
  if (r < 0.15 + 0.3 * e) {
    target = {
      x: rand(wa.x, wa.x + wa.width - SIZE),
      y: rand(wa.y, wa.y + wa.height - SIZE),
    };
    setState(Math.random() < e * 0.5 ? 'run' : 'walk', 20000);
  } else if (r < 0.55) {
    setState('sit', rand(2000, 5000));
  } else if (r < 0.55 + 0.25 * (1 - e)) {
    setState('sleep', rand(5000, 12000), '💤');
  } else if (Math.random() < personality.temper * 0.5) {
    setState('grumpy', 2500, '💢');
  } else {
    setState('idle', rand(1500, 3500));
  }
}

function tick() {
  const wa = screen.getPrimaryDisplay().workArea;
  if (Date.now() >= stateUntil) chooseNext(wa);

  if ((state === 'walk' || state === 'run') && target) {
    const dx = target.x - pos.x;
    const dy = target.y - pos.y;
    const dist = Math.hypot(dx, dy);
    const speed = state === 'run' ? 5 : 1.8;
    if (dist < speed) {
      target = null;
      setState('idle', rand(800, 2000));
    } else {
      pos.x += (dx / dist) * speed;
      pos.y += (dy / dist) * speed;
      if (Math.abs(dx) > 1) dir = dx > 0 ? 1 : -1;
    }
  }

  win.setBounds({ x: Math.round(pos.x), y: Math.round(pos.y), width: SIZE, height: SIZE });

  const key = JSON.stringify([state, dir, bubble, Math.round(fullness / 10)]);
  if (key !== lastSent) {
    lastSent = key;
    win.webContents.send('state', { state, dir, bubble, fullness });
  }
}

// ---------- AI：可选。配置了 apiUrl 才会调用 ----------
// 约定：POST { personality, event }，返回 { emoji: "😋" }
async function askAI(event) {
  if (!cfg.apiUrl) return null;
  try {
    const res = await fetch(cfg.apiUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ personality, event }),
      signal: AbortSignal.timeout(5000),
    });
    return await res.json();
  } catch (e) {
    return null;
  }
}

// ---------- 吃文件：监控文件夹，有文件消失就当它被删了 ----------
async function onFileDeleted(name, size) {
  const mb = size / 1048576;
  const gain = (8 + mb * 2) * (0.6 + personality.appetite * 0.8);
  fullness = Math.min(100, fullness + gain);
  target = null;
  setState('eat', Math.min(9000, 2500 + mb * 80), '😋');
  const ai = await askAI({
    type: 'file_deleted',
    name,
    sizeMB: Number(mb.toFixed(2)),
    fullness: Math.round(fullness),
  });
  if (ai && ai.emoji && state === 'eat') bubble = ai.emoji;
}

function watchDir(dir) {
  const sizes = new Map();
  try {
    for (const n of fs.readdirSync(dir)) {
      try {
        const st = fs.statSync(path.join(dir, n));
        if (st.isFile()) sizes.set(n, st.size);
      } catch (e) {}
    }
    fs.watch(dir, (ev, name) => {
      if (!name || /\.(crdownload|tmp|part)$/i.test(name) || name.startsWith('.')) return;
      fs.stat(path.join(dir, name), (err, st) => {
        if (!err) {
          if (st.isFile()) sizes.set(name, st.size);
        } else if (sizes.has(name)) {
          const size = sizes.get(name);
          sizes.delete(name);
          onFileDeleted(name, size);
        }
      });
    });
  } catch (e) {
    console.log('无法监控文件夹：', dir);
  }
}

// ---------- 和主人互动 ----------
ipcMain.on('poke', () => {
  target = null;
  if (Math.random() < personality.temper) setState('grumpy', 2500, '💢');
  else setState('happy', 2500, '💖');
});

ipcMain.on('menu', () => {
  Menu.buildFromTemplate([{ label: personality.name + '：再见', click: () => app.quit() }]).popup({
    window: win,
  });
});

// ---------- 启动 ----------
app.whenReady().then(() => {
  personality = loadPersonality();
  const wa = screen.getPrimaryDisplay().workArea;
  pos = { x: wa.x + wa.width / 2, y: wa.y + wa.height / 2 };

  win = new BrowserWindow({
    width: SIZE,
    height: SIZE,
    x: Math.round(pos.x),
    y: Math.round(pos.y),
    transparent: true,
    frame: false,
    alwaysOnTop: true,
    resizable: false,
    skipTaskbar: true,
    hasShadow: false,
    webPreferences: { preload: path.join(__dirname, 'preload.js') },
  });
  win.setAlwaysOnTop(true, 'screen-saver');
  win.setVisibleOnAllWorkspaces(true);
  win.loadFile('pet.html');
  win.webContents.on('did-finish-load', () => {
    lastSent = '';
  });

  setInterval(tick, 33);
  setInterval(() => {
    fullness = Math.max(0, fullness - 1);
  }, 1500);

  for (const d of cfg.watchDirs) watchDir(d.replace(/^~/, os.homedir()));
});

app.on('window-all-closed', () => app.quit());
