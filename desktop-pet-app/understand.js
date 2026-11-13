// 小模型 2：听懂主人的指令（中英双语，可扩展更多语言）
// 做法：把每个语言包里的例句拆成特征（中日韩按字和相邻两字，其他语言按词和相邻两词），
// 用朴素贝叶斯分类出"意图"。完全本地运行，不联网。
// 加新语言：不用改代码，往 lang/ 文件夹（或用户数据目录下的 lang/）放一个 JSON 语言包即可。
const fs = require('fs');
const path = require('path');

const CJK = /[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]+/gu;

function features(text) {
  const t = String(text).toLowerCase();
  const out = [];
  for (const run of t.match(CJK) || []) {
    const cs = [...run];
    for (let i = 0; i < cs.length; i++) {
      out.push('c:' + cs[i]);
      if (i > 0) out.push('d:' + cs[i - 1] + cs[i]);
    }
  }
  const words = t.replace(CJK, ' ').match(/[\p{L}\p{N}']+/gu) || [];
  for (let i = 0; i < words.length; i++) {
    out.push('w:' + words[i]);
    if (i > 0) out.push('b:' + words[i - 1] + '_' + words[i]);
  }
  return out;
}

class Understander {
  constructor(dirs) {
    this.dirs = dirs;
    this.load();
  }

  load() {
    const counts = {};
    const totals = {};
    const vocab = new Set();
    this.langs = [];
    for (const dir of this.dirs) {
      let files = [];
      try {
        files = fs.readdirSync(dir).filter((f) => f.endsWith('.json'));
      } catch (e) {
        continue;
      }
      for (const f of files) {
        try {
          const pack = JSON.parse(fs.readFileSync(path.join(dir, f), 'utf8'));
          this.langs.push(pack.language || f);
          for (const [intent, phrases] of Object.entries(pack.intents || {})) {
            counts[intent] = counts[intent] || {};
            totals[intent] = totals[intent] || 0;
            for (const ph of phrases) {
              for (const ft of features(ph)) {
                counts[intent][ft] = (counts[intent][ft] || 0) + 1;
                totals[intent] += 1;
                vocab.add(ft);
              }
            }
          }
        } catch (e) {
          console.log('语言包读取失败：', f);
        }
      }
    }
    this.counts = counts;
    this.totals = totals;
    this.vocab = vocab;
    this.intents = Object.keys(counts);
  }

  parse(text) {
    const all = features(text);
    const known = all.filter((f) => this.vocab.has(f));
    // 句子里认识的部分太少，说明说的是别的事，别乱猜
    if (!known.length || known.length / all.length < 0.3) return { intent: 'unknown', confidence: 0 };
    const A = 0.1; // 平滑系数取小，短指令（一两个词）才不会被稀释
    const V = this.vocab.size;
    const scores = this.intents.map((i) =>
      known.reduce((s, f) => s + Math.log(((this.counts[i][f] || 0) + A) / (this.totals[i] + A * V)), 0)
    );
    const m = Math.max(...scores);
    const exps = scores.map((s) => Math.exp(s - m));
    const sum = exps.reduce((a, b) => a + b, 0);
    const k = scores.indexOf(m);
    const confidence = exps[k] / sum;
    if (confidence < 0.6) return { intent: 'unknown', confidence };
    return { intent: this.intents[k], confidence };
  }
}

module.exports = { Understander, features };
