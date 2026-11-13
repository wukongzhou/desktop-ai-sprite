// 安全限制：精灵只在本机运行，只听主人的指令，不会超出主人的要求。
//  1. 不联网：默认不发任何网络请求。只有在 config.json 里把 allowNetwork 设为 true 并填了 apiUrl 才会连，
//     而且远程地址必须是 https。
//  2. 只听主人：指令只接受精灵自己窗口的输入框，并限制长度和频率。
//  3. 只做白名单里的事：意图必须在 skills 白名单里，AI 返回的内容也要过这一关；语言包只能加说法，不能加新本领。
//  4. 不动你的文件：监控文件夹是只读的，从不删除、修改、移动、上传任何文件；只写自己的数据目录。
//  5. 不碰别的：不读屏幕、键盘、麦克风、摄像头，不执行命令，不下载代码，不打开网页。
//  6. 学习有边界：只调整动作偏好和心情，数值有上下限，不会改自己的代码。
const path = require('path');

function allowedIntent(intent, allowed) {
  return typeof intent === 'string' && allowed.includes(intent);
}

// AI 返回的表情只收 emoji，最多 8 个字符，其他一律丢掉
function cleanEmoji(s) {
  if (typeof s !== 'string') return '';
  const t = [...s.trim()].slice(0, 8).join('');
  return /^[\p{Extended_Pictographic}\uFE0F\u200D]+$/u.test(t) ? t : '';
}

function insideDir(base, target) {
  const rel = path.relative(path.resolve(base), path.resolve(target));
  return rel === '' || (!rel.startsWith('..') && !path.isAbsolute(rel));
}

// 远程地址必须 https；本机地址（localhost）允许 http
function validApiUrl(u) {
  try {
    const x = new URL(u);
    if (x.protocol === 'https:') return true;
    return x.protocol === 'http:' && (x.hostname === 'localhost' || x.hostname === '127.0.0.1');
  } catch (e) {
    return false;
  }
}

// 滑动窗口限流：ms 毫秒内最多 max 次
function makeLimiter(max, ms) {
  let ts = [];
  return () => {
    const n = Date.now();
    ts = ts.filter((t) => n - t < ms);
    if (ts.length >= max) return false;
    ts.push(n);
    return true;
  };
}

module.exports = { allowedIntent, cleanEmoji, insideDir, validApiUrl, makeLimiter };
