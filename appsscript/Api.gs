/**
 * ==================================================================
 *  Api.gs - connects this MCQ Test System to SkillOps by Robomy.
 *
 *  Add this ONE file to the Apps Script project (File > New > Script, name it "Api").
 *  Do not change Code.gs or the HTML files.
 *
 *  One-time set-up:
 *   1. In the Apps Script editor choose the function setupApi and press Run.
 *      Open View > Executions (or the log at the bottom): it shows the secret ONCE. Copy it.
 *   2. Deploy > Manage deployments > edit (pencil) > Version: New version > Deploy.
 *      (The web app link that ends in /exec stays the same.)
 *   3. In SkillOps: MCQ tests > this institution > paste the /exec link and the secret >
 *      Test connection.
 *
 *  How it works: SkillOps sends a POST with JSON {secret, action, args, idem}. A wrong or missing
 *  secret gets an error and nothing is done. "idem" makes a repeated request (a retry after a slow
 *  network) do its work only once: the answer is remembered for 10 minutes.
 *  It reuses the functions in Code.gs. Three menu actions in Code.gs (re-attempt, re-mark, marks
 *  register) ask questions in pop-up boxes, which a web request cannot show, so their core is
 *  repeated here without the pop-ups.
 * ==================================================================
 */

const API_SECRET_PROP = 'SKILLOPS_API_SECRET';
const API_VERSION = 1;

/** Run once from the editor. Makes the secret, keeps it in Script Properties, shows it once. */
function setupApi() {
  const props = PropertiesService.getScriptProperties();
  if (props.getProperty(API_SECRET_PROP)) {
    Logger.log('A SkillOps secret already exists, so nothing was changed. To make a new one, run resetApiSecret ' +
      '(then paste the new secret into SkillOps; the old one stops working).');
    return;
  }
  const secret = Utilities.getUuid().replace(/-/g, '') + Utilities.getUuid().replace(/-/g, '');
  props.setProperty(API_SECRET_PROP, secret);
  Logger.log('SkillOps secret (copy it now; it is not shown again): ' + secret);
  try { getSettings_(); } catch (e) { Logger.log('Note: run "MCQ Tests > Set up / repair sheets" first if the sheets are new.'); }
}

/** Makes a new secret. The old one stops working at once. */
function resetApiSecret() {
  PropertiesService.getScriptProperties().deleteProperty(API_SECRET_PROP);
  setupApi();
}

function apiJson_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

function apiSafeEqual_(a, b) {
  a = String(a || ''); b = String(b || '');
  if (!a || a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function doPost(e) {
  let body;
  try {
    body = JSON.parse((e && e.postData && e.postData.contents) || '{}');
  } catch (err) {
    return apiJson_({ ok: false, code: 'bad_json', message: 'The request was not JSON.' });
  }
  const secret = PropertiesService.getScriptProperties().getProperty(API_SECRET_PROP);
  if (!secret || !apiSafeEqual_(secret, body.secret)) {
    return apiJson_({ ok: false, code: 'bad_secret', message: 'Wrong or missing secret. Run setupApi and paste the secret into SkillOps.' });
  }
  const fn = API_ACTIONS[body.action];
  if (!fn) return apiJson_({ ok: false, code: 'unknown_action', message: 'Unknown action: ' + body.action });

  const cache = CacheService.getScriptCache();
  const idem = String(body.idem || '').slice(0, 80);
  if (idem) {
    const seen = cache.get('skillops_idem_' + idem);
    if (seen) return ContentService.createTextOutput(seen).setMimeType(ContentService.MimeType.JSON);
  }
  let out;
  try {
    out = { ok: true, version: API_VERSION, result: fn(body.args || {}) };
  } catch (err) {
    out = { ok: false, code: 'error', message: String((err && err.message) || err) };
  }
  const text = JSON.stringify(out);
  if (idem && out.ok && text.length < 90000) cache.put('skillops_idem_' + idem, text, 600);
  return ContentService.createTextOutput(text).setMimeType(ContentService.MimeType.JSON);
}

// ------------------------------------------------------------------ helpers

function apiBatches_(args) {
  return (args.batches || []).map(function (b) { return String(b).trim().toUpperCase(); }).filter(String);
}

/** email -> student, for the given batches (all students if no batch is given). */
function apiRoster_(batches) {
  const out = {};
  getStudents_().forEach(function (s) {
    if (!batches.length || batches.indexOf(s.batch.toUpperCase()) >= 0) out[s.email] = s;
  });
  return out;
}

function apiCheckSession_(sessionId, batches) {
  const s = getSessionById_(sessionId);
  if (!s) throw new Error('That attempt was not found.');
  if (batches.length) {
    const st = getStudent_(s.email);
    if (!st || batches.indexOf(st.batch.toUpperCase()) < 0) throw new Error('That student is not in your batch.');
  }
  return s;
}

/**
 * A trainer may change only tests meant for their own batch: the test's Allowed Batches must name it.
 * Shared tests (Allowed Batches empty) are changed by the supervisor (SkillOps sends no batches then).
 */
function apiCheckTestForBatches_(test, batches) {
  if (!batches.length) return;
  if (!test.batches.length) throw new Error('Test ' + test.id + ' is shared by all batches. Ask your supervisor to change it.');
  if (!test.batches.some(function (b) { return batches.indexOf(b) >= 0; })) {
    throw new Error('Test ' + test.id + ' is not for your batch.');
  }
}

function apiResultRows_(testId, roster) {
  const sh = SpreadsheetApp.getActive().getSheetByName(RESULT_PREFIX + String(testId).toUpperCase()) ||
    SpreadsheetApp.getActive().getSheetByName(RESULT_PREFIX + testId);
  if (!sh || sh.getLastRow() < 2) return [];
  return sh.getRange(2, 1, sh.getLastRow() - 1, R_COLS.length).getValues().filter(function (r) {
    return normEmail_(r[1]) && roster[normEmail_(r[1])];
  }).map(function (r) {
    return { submittedAt: toMs_(r[0]), email: normEmail_(r[1]), name: String(r[2]), batch: roster[normEmail_(r[1])].batch,
      score: Number(r[3]), max: Number(r[4]), percentage: Number(r[5]), correct: Number(r[6]), wrong: Number(r[7]),
      unanswered: Number(r[8]), minutes: Number(r[9]), warnings: Number(r[10]), type: String(r[11]),
      earlier: String(r[11]).indexOf('RE-ATTEMPT ALLOWED') >= 0, sessionId: String(r[13]) };
  });
}

// ------------------------------------------------------------------ actions

const API_ACTIONS = {
  ping: function () {
    const s = getSettings_();
    let quota = null;
    try { quota = MailApp.getRemainingDailyQuota(); } catch (e) { quota = null; }
    return { institute: s.institute, method: s.method, tests: getTests_().length, emailQuota: quota,
      sheet: SpreadsheetApp.getActive().getName(), now: Date.now() };
  },

  listTests: function (args) {
    const counts = {};
    getAllQuestions_().forEach(function (q) { counts[q.testKey] = (counts[q.testKey] || 0) + 1; });
    const batches = apiBatches_(args);
    return getTests_().filter(function (t) {
      return !batches.length || !t.batches.length || t.batches.some(function (b) { return batches.indexOf(b) >= 0; });
    }).map(function (t) {
      return { id: t.id, title: t.title, active: t.active, duration: t.duration, perStudent: t.perStudent,
        questions: counts[t.id.toUpperCase()] || 0, batches: t.batches, accessCode: t.code ? 'yes' : '',
        maxWarnings: t.maxWarnings, showScore: t.showScore };
    });
  },

  setTestActive: function (args) {
    const id = String(args.testId || '').trim().toUpperCase();
    const test = getTest_(id);
    if (!test) throw new Error('There is no test with the ID ' + id + '.');
    apiCheckTestForBatches_(test, apiBatches_(args));
    const lock = lock_();
    try {
      const t = table_(SH.TESTS, false);
      for (let i = 0; i < t.rows.length; i++) {
        if (String(t.rows[i][t.ix['Test ID']]).trim().toUpperCase() === id) {
          t.sh.getRange(i + 2, t.ix['Active'] + 1).setValue(args.active ? 'YES' : 'NO');
          return { testId: id, active: !!args.active };
        }
      }
    } finally {
      lock.releaseLock();
    }
    throw new Error('There is no test with the ID ' + id + '.');
  },

  getMonitor: function (args) {
    const batches = apiBatches_(args);
    const data = getMonitorData(getSettings_().key, args.testId);
    const roster = apiRoster_(batches);
    data.sessions = data.sessions.filter(function (s) { return roster[normEmail_(s.email)]; });
    data.events = data.events.filter(function (e) { return roster[normEmail_(e.email)]; });
    data.codes = data.codes.filter(function (c) { return roster[normEmail_(c.email)]; });
    data.notStarted = Object.keys(roster).filter(function (em) {
      return roster[em].active && !data.sessions.some(function (s) { return normEmail_(s.email) === em; });
    }).map(function (em) { return { email: em, name: roster[em].name }; });
    return data;
  },

  addTime: function (args) {
    apiCheckSession_(args.sessionId, apiBatches_(args));
    trainerAddTime(getSettings_().key, args.sessionId, args.minutes);
    return { done: true };
  },

  endTest: function (args) {
    apiCheckSession_(args.sessionId, apiBatches_(args));
    trainerEndTest(getSettings_().key, args.sessionId);
    return { done: true };
  },

  results: function (args) {
    const rows = apiResultRows_(args.testId, apiRoster_(apiBatches_(args)));
    return { testId: String(args.testId).toUpperCase(), rows: rows };
  },

  /** Per batch: tests written and the latest test's average percentage (for the SkillOps dashboard). */
  summary: function (args) {
    const roster = apiRoster_(apiBatches_(args));
    const out = { tests: [], latest: null };
    getTests_().forEach(function (t) {
      const rows = apiResultRows_(t.id, roster).filter(function (r) { return !r.earlier; });
      if (!rows.length) return;
      const avg = rows.reduce(function (a, r) { return a + r.percentage; }, 0) / rows.length;
      const last = Math.max.apply(null, rows.map(function (r) { return r.submittedAt; }));
      out.tests.push({ id: t.id, title: t.title, written: rows.length, average: Math.round(avg * 10) / 10, last: last });
    });
    out.tests.sort(function (a, b) { return b.last - a.last; });
    out.latest = out.tests[0] || null;
    const batches = apiBatches_(args);
    out.open = getTests_().filter(function (t) {
      return t.active && (!batches.length || !t.batches.length || t.batches.some(function (b) { return batches.indexOf(b) >= 0; }));
    }).map(function (t) { return { id: t.id, title: t.title }; });
    return out;
  },

  /** summary for each batch in one call: {"A": {...}, "B": {...}} (for the SkillOps dashboard). */
  batchSummaries: function (args) {
    const out = {};
    apiBatches_(args).forEach(function (b) { out[b] = API_ACTIONS.summary({ batches: [b] }); });
    return out;
  },

  /** The marks register (as in "Build marks register"), for the given batches, returned as data. */
  marksRegister: function (args) {
    const roster = apiRoster_(apiBatches_(args));
    const sheets = SpreadsheetApp.getActive().getSheets().filter(function (s) { return s.getName().indexOf(RESULT_PREFIX) === 0; })
      .sort(function (a, b) { return a.getName().localeCompare(b.getName(), undefined, { numeric: true }); });
    const students = {}, tests = [];
    sheets.forEach(function (sh) {
      const id = sh.getName().slice(RESULT_PREFIX.length);
      let max = 0;
      const maxes = {};
      apiResultRows_(id, roster).forEach(function (r) {
        if (r.earlier) return;
        const st = students[r.email] || (students[r.email] = { name: r.name, marks: {} });
        st.marks[id] = { score: r.score, max: r.max };
        if (r.max > max) max = r.max;
        maxes[r.max] = 1;
      });
      if (max > 0) tests.push({ id: id, max: max, pct: Object.keys(maxes).length > 1 });
    });
    Object.keys(roster).forEach(function (e) {
      if (roster[e].active && !students[e]) students[e] = { name: roster[e].name, marks: {} };
    });
    const header = ['Name', 'Email', 'Batch'].concat(tests.map(function (t) { return t.id + (t.pct ? ' (%)' : ' (out of ' + t.max + ')'); }))
      .concat(['Tests Written', 'Average %']);
    const rows = Object.keys(students).sort(function (a, b) {
      return (roster[a] ? roster[a].batch : '').localeCompare(roster[b] ? roster[b].batch : '') ||
        students[a].name.localeCompare(students[b].name);
    }).map(function (email) {
      const st = students[email];
      let n = 0, pctSum = 0;
      const cells = tests.map(function (t) {
        const m = st.marks[t.id];
        if (!m) return 'ABSENT';
        n++; pctSum += m.max ? (m.score / m.max) * 100 : 0;
        return t.pct ? (m.max ? Math.round(m.score / m.max * 1000) / 10 : 0) : m.score;
      });
      return [roster[email] ? roster[email].name : st.name, email, roster[email] ? roster[email].batch : '']
        .concat(cells).concat([n, n ? Math.round(pctSum / n * 10) / 10 : '']);
    });
    return { header: header, rows: rows };
  },

  /** A trainer may add questions to their own batch's tests, or make a new test (it is then for their batch only). */
  importQuestions: function (args) {
    const id = String(args.testId || '').trim().toUpperCase();
    const batches = apiBatches_(args);
    const existing = getTest_(id);
    if (existing) apiCheckTestForBatches_(existing, batches);
    const result = importQuestions(id, String(args.csv || ''), args.mode === 'replace' ? 'replace' : 'add');
    if (!existing && batches.length && result && result.ok) {
      const lock = lock_();
      try {
        const t = table_(SH.TESTS, false);
        for (let i = 0; i < t.rows.length; i++) {
          if (String(t.rows[i][t.ix['Test ID']]).trim().toUpperCase() === id) {
            t.sh.getRange(i + 2, t.ix['Allowed Batches'] + 1).setValue(batches.join(', '));
            break;
          }
        }
      } finally {
        lock.releaseLock();
      }
    }
    return result;
  },

  /** As the menu item "Allow a student to re-attempt a test", without the pop-ups. */
  allowReattempt: function (args) {
    const test = getTest_(args.testId);
    if (!test) throw new Error('There is no test with that ID.');
    const email = normEmail_(args.email);
    const batches = apiBatches_(args);
    if (batches.length) {
      const st = getStudent_(email);
      if (!st || batches.indexOf(st.batch.toUpperCase()) < 0) throw new Error('That student is not in your batch.');
    }
    const ids = [];
    const lock = lock_();
    try {
      const sh = sheet_(SH.SESSIONS);
      if (sh.getLastRow() > 1) {
        sh.getRange(2, 1, sh.getLastRow() - 1, S_COLS.length).getValues().forEach(function (r, i) {
          if (String(r[SC['Test ID']]).toUpperCase() === test.id.toUpperCase() &&
              normEmail_(r[SC['Email']]) === email && String(r[SC['Status']]) !== STATUS.RESET) {
            const s = rowToSession_(r, i + 2);
            setCells_(s, { 'Status': STATUS.RESET });
            logEvent_(s, 'Trainer allowed a re-attempt', 'Earlier status: ' + s.status + ' (from SkillOps)', false);
            ids.push(s.id);
          }
        });
      }
      if (ids.length) {
        const rs = ensureResultsSheet_(test.id);
        if (rs.getLastRow() > 1) {
          rs.getRange(2, 1, rs.getLastRow() - 1, R_COLS.length).getValues().forEach(function (r, i) {
            if (ids.indexOf(String(r[13])) >= 0 && String(r[11]).indexOf(REATTEMPT_TAG) < 0) {
              rs.getRange(i + 2, 12).setValue(String(r[11]) + REATTEMPT_TAG);
            }
          });
        }
      }
    } finally {
      lock.releaseLock();
    }
    return { allowed: ids.length > 0, attempts: ids.length };
  },

  /** As the menu item "Re-mark a test", without the pop-ups. */
  regradeTest: function (args) {
    const test = getTest_(args.testId);
    if (!test) throw new Error('There is no test with that ID.');
    const lock = lock_();
    try {
      const sh = sheet_(SH.SESSIONS);
      const rows = sh.getLastRow() > 1 ? sh.getRange(2, 1, sh.getLastRow() - 1, S_COLS.length).getValues() : [];
      const out = [];
      let writing = 0, changed = 0;
      rows.forEach(function (r, i) {
        if (String(r[SC['Test ID']]).toUpperCase() !== test.id.toUpperCase()) return;
        const s = rowToSession_(r, i + 2);
        if (s.status === STATUS.IN_PROGRESS) { writing++; return; }
        if (s.status === STATUS.RESET && s.score === '') return;
        const g = grade_(s, s.answers, test);
        if (s.status !== STATUS.RESET && Number(s.score) !== g.score) { setCells_(s, { 'Score': g.score }); changed++; }
        const type = s.status === STATUS.RESET ? 'EARLIER ATTEMPT' + REATTEMPT_TAG : s.status;
        out.push(resultRow_(s, g, type, new Date(s.lastSeen)));
      });
      if (writing) return { done: false, message: writing + ' student(s) are still writing this test. Re-mark after everyone has finished.' };
      const rs = ensureResultsSheet_(test.id);
      if (rs.getLastRow() > 1) rs.getRange(2, 1, rs.getLastRow() - 1, R_COLS.length).clearContent();
      if (out.length) rs.getRange(2, 1, out.length, R_COLS.length).setValues(out);
      return { done: true, message: 'Re-marked ' + out.length + ' attempt(s). Scores changed for ' + changed + ' student(s).' };
    } finally {
      lock.releaseLock();
    }
  },

  links: function () {
    const s = getSettings_();
    let url = s.url;
    if (!url) { try { url = ScriptApp.getService().getUrl(); } catch (e) { url = ''; } }
    return { studentUrl: url, monitorUrl: url ? url + '?page=monitor&key=' + encodeURIComponent(s.key) : '' };
  }
};
