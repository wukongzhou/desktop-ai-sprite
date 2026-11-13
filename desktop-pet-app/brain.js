// 小模型 1：随机动作 + 性格 + 学习
// 每个动作的"想做的程度" = 性格决定的先验 × exp(学到的偏好)。
// 主人夸它或戳它（它开心的时候），当时正在做的动作偏好上升；被骂则下降。
// 偏好按时段（凌晨/上午/下午/晚上）分开学，所以它会慢慢养成"这个点爱干什么"的习惯。
// 心情也会变：被夸变好，被骂变差，随时间慢慢回到平静。心情差、脾气大的时候更容易不听话。
const fs = require('fs');

const ACTIONS = ['walk', 'run', 'sit', 'sleep', 'idle', 'grumpy', 'dance'];
const clamp = (x, a, b) => Math.max(a, Math.min(b, x));

function bucket(d = new Date()) {
  const h = d.getHours();
  return h < 6 ? 'night' : h < 12 ? 'morning' : h < 18 ? 'afternoon' : 'evening';
}

function prior(a, p, mood) {
  switch (a) {
    case 'walk': return 0.8 + 1.2 * p.energy;
    case 'run': return 0.2 + 1.2 * p.energy * p.energy;
    case 'sit': return 1.0;
    case 'sleep': return 0.4 + 1.2 * (1 - p.energy);
    case 'idle': return 0.8;
    case 'grumpy': return 0.05 + 0.9 * p.temper * (1 - mood);
    case 'dance': return 0.1 + 0.6 * mood;
    default: return 0.5;
  }
}

class Brain {
  constructor(file) {
    this.file = file;
    this.pref = {};
    this.mood = 0.5;
    this.last = 'idle';
    this._timer = null;
    try {
      const d = JSON.parse(fs.readFileSync(file, 'utf8'));
      this.pref = d.pref || {};
      this.mood = typeof d.mood === 'number' ? d.mood : 0.5;
    } catch (e) {
      /* 第一次启动，没有记忆 */
    }
  }

  choose(p) {
    const pr = this.pref[bucket()] || (this.pref[bucket()] = {});
    const w = ACTIONS.map((a) => prior(a, p, this.mood) * Math.exp(pr[a] || 0));
    let r = Math.random() * w.reduce((s, x) => s + x, 0);
    let i = 0;
    while (i < w.length - 1 && (r -= w[i]) > 0) i++;
    for (const k in pr) pr[k] *= 0.995; // 慢慢淡忘，保持一点好奇心
    this.last = ACTIONS[i];
    return this.last;
  }

  reward(r, current) {
    const a = ACTIONS.includes(current) ? current : this.last;
    const pr = this.pref[bucket()] || (this.pref[bucket()] = {});
    pr[a] = clamp((pr[a] || 0) + 0.3 * r, -2, 2);
    this.mood = clamp(this.mood + (r > 0 ? 0.1 : -0.15) * Math.abs(r), 0, 1);
    this.save();
  }

  drift() {
    this.mood += (0.5 - this.mood) * 0.01;
  }

  refuseChance(p) {
    return clamp(p.temper * 0.35 * (1.2 - this.mood), 0, 0.5);
  }

  save() {
    clearTimeout(this._timer);
    this._timer = setTimeout(() => this.saveNow(), 1000);
  }

  saveNow() {
    try {
      fs.writeFileSync(this.file, JSON.stringify({ pref: this.pref, mood: this.mood }, null, 2));
    } catch (e) {}
  }
}

module.exports = { Brain, ACTIONS, bucket };
