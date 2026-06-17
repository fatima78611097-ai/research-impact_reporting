import * as pdfjsLib from '/dashboard/static/vendor/pdfjs/pdf.min.mjs';
import * as pdfjsViewer from '/dashboard/static/vendor/pdfjs/pdf_viewer.mjs';

// --- Configuration ---
const MAX_QUERY_LEN = 80;
const URL_EXPIRY_MS = 15 * 60 * 1000;

// --- State ---
let pdfViewer = null;
let findController = null;
let eventBus = null;
let lockedRow = null;
let findResolve = null;
// Monotonic token: every locate increments it. A locate only writes its result
// (highlight, scroll, or not-found dot) if it is still the newest one — so a
// superseded search can never stamp a stale/false dot on a row.
let searchSeq = 0;
const rowState = new Map();
// key -> { query, page } for rows already located, so re-clicking re-highlights
// from the known-good phrase instead of re-running the whole phrase fallback.
const foundInfo = new Map();

// --- Initialization ---
document.addEventListener('DOMContentLoaded', init);

function init() {
  const config = window.QA_CONFIG;
  if (!config || !config.pdfUrl) {
    document.getElementById('pdf-error').style.display = 'flex';
    return;
  }

  pdfjsLib.GlobalWorkerOptions.workerSrc = config.workerSrc;

  const container = document.getElementById('pdf-container');
  eventBus = new pdfjsViewer.EventBus();
  const linkService = new pdfjsViewer.PDFLinkService({ eventBus });
  findController = new pdfjsViewer.PDFFindController({ eventBus, linkService });

  pdfViewer = new pdfjsViewer.PDFViewer({
    container,
    eventBus,
    linkService,
    findController,
    textLayerMode: 2,
  });
  linkService.setViewer(pdfViewer);

  eventBus.on('pagesinit', () => {
    pdfViewer.currentScaleValue = 'page-width';
  });

  // A found match resolves via the matches-count event (only fired when a page
  // actually has matches); a miss resolves via the control-state NOT_FOUND event.
  eventBus.on('updatefindmatchescount', (e) => {
    if (findResolve) {
      const found = e.matchesCount && e.matchesCount.total > 0;
      findResolve({ found });
      findResolve = null;
    }
  });

  eventBus.on('updatefindcontrolstate', (e) => {
    if (findResolve && e.state === 1) { // 1 = NOT_FOUND
      findResolve({ found: false });
      findResolve = null;
    }
  });

  const loadingTask = pdfjsLib.getDocument(config.pdfUrl);
  loadingTask.promise.then((pdfDoc) => {
    pdfViewer.setDocument(pdfDoc);
    linkService.setDocument(pdfDoc, null);
  }).catch((err) => {
    console.error('PDF load failed:', err);
    const elapsed = Date.now() - config.pageLoadTime;
    if (elapsed > URL_EXPIRY_MS) {
      document.getElementById('pdf-expired').style.display = 'flex';
    } else {
      document.getElementById('pdf-error').style.display = 'flex';
    }
  });

  setupRowListeners();
}

// --- Row Interaction ---
// Locating is click-driven only. Hovering does not search — that previously
// fired a live find per row as the mouse swept the list, and the colliding
// searches produced false "not found" dots.
function setupRowListeners() {
  document.querySelectorAll('.qa-row').forEach((row) => {
    row.addEventListener('click', () => onRowClick(row));
  });
}

function onRowClick(row) {
  // Click an already-locked row to clear it.
  if (lockedRow === row) {
    row.classList.remove('qa-row-active');
    lockedRow = null;
    clearHighlight();
    return;
  }

  if (lockedRow) {
    lockedRow.classList.remove('qa-row-active');
  }
  lockedRow = row;
  row.classList.add('qa-row-active');

  // A deliberate click always re-attempts: drop a stale not-found verdict so the
  // row gets a fresh search rather than staying stuck on an earlier miss.
  const key = row.dataset.type + '-' + row.dataset.index;
  if (rowState.get(key) === 'not-found') {
    rowState.delete(key);
    row.classList.remove('qa-row-not-found');
  }

  locateRow(row);
}

// --- Snippet Search ---
function getSnippet(row) {
  const type = row.dataset.type;
  const index = parseInt(row.dataset.index, 10);
  const dataEl = document.getElementById(type === 'metric' ? 'metrics-data' : 'stories-data');
  if (!dataEl) return '';
  const items = JSON.parse(dataEl.textContent);
  return (items[index] && items[index].source_snippet) || '';
}

// Build search phrases ordered most-likely-correct first. The guiding rule:
// NEVER search a bare short number — a value like "2" or "31" matches every
// occurrence on the page and lights up everything. Anchor on distinctive label
// text or an effectively-unique number instead.
function extractSearchPhrase(snippet) {
  if (!snippet) return [];
  const clean = snippet.replace(/\s+/g, ' ').trim();
  if (!clean) return [];

  const phrases = [];
  const seen = new Set();
  const add = (p, minLen = 4) => {
    p = (p || '').replace(/^[^A-Za-z0-9$]+|[^A-Za-z0-9%]+$/g, '').trim();
    if (p.length >= minLen && !seen.has(p)) {
      seen.add(p);
      phrases.push(p);
    }
  };

  // 1) The whole snippet — matches when the source text is actually contiguous.
  add(clean.length <= MAX_QUERY_LEN ? clean : clean.slice(0, MAX_QUERY_LEN));

  // 2) "Strong" numbers only: comma-grouped (253,903), 4+ digits, or decimals.
  //    These are effectively unique on a page. Short integers (2, 31, 170, 813)
  //    are deliberately excluded — searching them lights up the whole page.
  (clean.match(/\d{1,3}(?:,\d{3})+|\d{4,}|\d+\.\d+/g) || [])
    .sort((a, b) => b.length - a.length)
    .forEach((n) => add(n));

  // 3) Alphabetic label-runs (split on numbers and separators), longest first.
  //    Labels like "PEPIN COUNTY" or "phone calls answered" appear verbatim in
  //    the document even when the value sits in a separate cell, so they reliably
  //    anchor to the right region of the page.
  clean
    .replace(/\d[\d,.]*/g, '|')
    .split(/[|:;•]+/)
    .map((s) => s.trim())
    .filter((s) => s.split(/\s+/).length >= 2 && s.replace(/\s/g, '').length >= 7)
    .sort((a, b) => b.length - a.length)
    .forEach((s) => add(s));

  // 4) A value with its unit ("31%", "$1,200", "9.7%") — distinctive enough to
  //    try as a last resort, unlike the bare number.
  const valUnit = clean.match(/\$\s?[\d,]+(?:\.\d+)?|\d[\d,]*\.?\d*\s?%/);
  if (valUnit) add(valUnit[0].replace(/\s+/g, ''), 2);

  return phrases;
}

async function locateRow(row) {
  const key = row.dataset.type + '-' + row.dataset.index;
  const mySeq = ++searchSeq;
  const isCurrent = () => searchSeq === mySeq && lockedRow === row;

  // Already located: re-highlight from the known-good phrase and re-scroll.
  const cached = foundInfo.get(key);
  if (cached) {
    await doFind(cached.query);
    if (isCurrent() && cached.page > 0) scrollToMatch(cached.page);
    return;
  }

  const snippet = getSnippet(row);
  if (!snippet) {
    markNotFound(row, key, mySeq, 'No source snippet available');
    return;
  }
  const phrases = extractSearchPhrase(snippet);
  if (phrases.length === 0) {
    markNotFound(row, key, mySeq, 'No searchable text in snippet');
    return;
  }

  row.classList.add('qa-row-searching');
  row.classList.remove('qa-row-not-found');

  let matchedQuery = null;
  let matchPage = -1;
  for (const query of phrases) {
    const result = await doFind(query);
    // Superseded by a newer click — write nothing (no stale highlight, no dot).
    if (searchSeq !== mySeq) {
      row.classList.remove('qa-row-searching');
      return;
    }
    if (result.found) {
      matchedQuery = query;
      const sel = findController.selected;
      matchPage = sel && sel.pageIdx >= 0 ? sel.pageIdx + 1 : -1;
      break;
    }
  }

  row.classList.remove('qa-row-searching');

  if (matchedQuery === null) {
    markNotFound(row, key, mySeq, 'Source text not found in PDF (often an infographic image)');
  } else {
    rowState.set(key, 'found');
    foundInfo.set(key, { query: matchedQuery, page: matchPage });
    row.classList.remove('qa-row-not-found');
    if (isCurrent() && matchPage > 0) scrollToMatch(matchPage);
  }
}

function markNotFound(row, key, seq, msg) {
  row.classList.remove('qa-row-searching');
  // Only the newest search may stamp the dot, so superseded searches stay silent.
  if (searchSeq !== seq) return;
  rowState.set(key, 'not-found');
  row.classList.add('qa-row-not-found');
  row.setAttribute('title', msg);
}

// Bring the matched page into view (rendering it if needed), then center the
// painted find-highlight. The find controller's own scroll only fires if the
// page was already rendered at match time, which it usually is not.
async function scrollToMatch(pageNum) {
  if (!pageNum || pageNum < 1 || !pdfViewer) return;
  try {
    pdfViewer.scrollPageIntoView({ pageNumber: pageNum });
  } catch (e) {
    /* page not ready yet; the poll below still recovers once it renders */
  }
  for (let i = 0; i < 25; i++) {
    const el =
      document.querySelector(`.page[data-page-number="${pageNum}"] .textLayer .highlight.selected`) ||
      document.querySelector(`.page[data-page-number="${pageNum}"] .textLayer .highlight`);
    if (el) {
      el.scrollIntoView({ block: 'center', behavior: 'smooth' });
      return;
    }
    await new Promise((r) => setTimeout(r, 80));
  }
}

function doFind(query) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (result) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      if (findResolve === finish) findResolve = null;
      resolve(result);
    };
    const timeout = setTimeout(() => finish({ found: false }), 3000);
    findResolve = finish;

    // PDF.js v3+ removed PDFFindController.executeCommand; a search is triggered
    // by dispatching a 'find' event on the eventBus (the controller listens for it).
    eventBus.dispatch('find', {
      source: null,
      type: '',
      query: query,
      highlightAll: true,
      caseSensitive: false,
      entireWord: false,
      findPrevious: false,
    });
  });
}

function clearHighlight() {
  if (!eventBus) return;
  eventBus.dispatch('find', {
    source: null,
    type: '',
    query: '',
    highlightAll: false,
    caseSensitive: false,
    entireWord: false,
    findPrevious: false,
  });
}
