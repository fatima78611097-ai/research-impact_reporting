import * as pdfjsLib from '/dashboard/static/vendor/pdfjs/pdf.min.mjs';
import * as pdfjsViewer from '/dashboard/static/vendor/pdfjs/pdf_viewer.mjs';

// --- Configuration ---
const MAX_QUERY_LEN = 80;
const DEBOUNCE_MS = 50;
const URL_EXPIRY_MS = 15 * 60 * 1000;

// --- State ---
let pdfViewer = null;
let findController = null;
let eventBus = null;
let lockedRow = null;
let hoveredRow = null;
let debounceTimer = null;
let findResolve = null;
const rowState = new Map();

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

  eventBus.on('updatefindmatchescount', (e) => {
    if (findResolve) {
      const found = e.matchesCount && e.matchesCount.total > 0;
      findResolve({ found });
      findResolve = null;
    }
  });

  eventBus.on('updatefindcontrolstate', (e) => {
    if (findResolve && e.state === 1) {
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
function setupRowListeners() {
  document.querySelectorAll('.qa-row').forEach((row) => {
    row.addEventListener('mouseenter', () => onRowHover(row));
    row.addEventListener('mouseleave', () => onRowLeave(row));
    row.addEventListener('click', (e) => onRowClick(row, e));
  });
}

function onRowHover(row) {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => {
    if (lockedRow) return;
    hoveredRow = row;
    highlightRow(row);
  }, DEBOUNCE_MS);
}

function onRowLeave(_row) {
  clearTimeout(debounceTimer);
  hoveredRow = null;
  if (!lockedRow) {
    clearHighlight();
  }
}

function onRowClick(row, _e) {
  clearTimeout(debounceTimer);

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
  highlightRow(row);
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

function extractSearchPhrase(snippet) {
  if (!snippet) return [];
  const clean = snippet.replace(/\s+/g, ' ').trim();
  if (!clean) return [];

  const phrases = [];

  // Step 1: Prefer a numeric-rich substring (40-60 chars)
  const numMatch = clean.match(/\d[\d,.\s]{0,10}\d/);
  if (numMatch) {
    const numIdx = numMatch.index;
    const start = Math.max(0, numIdx - 20);
    const end = Math.min(clean.length, numIdx + numMatch[0].length + 30);
    const sub = clean.slice(start, end).trim();
    if (sub.length > MAX_QUERY_LEN) {
      phrases.push(sub.slice(0, MAX_QUERY_LEN));
    } else if (sub.length >= 10) {
      phrases.push(sub);
    }
  }

  // If no numeric substring, take middle 40-60 chars
  if (phrases.length === 0) {
    if (clean.length <= MAX_QUERY_LEN) {
      phrases.push(clean);
    } else {
      const mid = Math.floor(clean.length / 2);
      const start = Math.max(0, mid - 25);
      phrases.push(clean.slice(start, start + 50).trim());
    }
  }

  // Step 2: Shorter 20-30 char fallback
  if (numMatch) {
    const numSub = clean.slice(
      Math.max(0, numMatch.index - 5),
      Math.min(clean.length, numMatch.index + numMatch[0].length + 15)
    ).trim();
    if (numSub.length >= 8 && numSub !== phrases[0]) {
      phrases.push(numSub.slice(0, 30));
    }
  } else if (clean.length > 30) {
    phrases.push(clean.slice(0, 25).trim());
  }

  // Step 3: Number-only fallback
  const numbers = clean.match(/[\d,]+\.?\d*/g);
  if (numbers) {
    const longest = numbers.sort((a, b) => b.length - a.length)[0];
    if (longest && !phrases.includes(longest)) {
      phrases.push(longest);
    }
  }

  return phrases;
}

async function highlightRow(row) {
  const key = row.dataset.type + '-' + row.dataset.index;
  const state = rowState.get(key);
  if (state === 'searching') return;

  if (state === 'not-found') {
    row.classList.add('qa-row-not-found');
    return;
  }

  const snippet = getSnippet(row);
  if (!snippet) {
    row.classList.add('qa-row-not-found');
    row.setAttribute('title', 'No source snippet available');
    rowState.set(key, 'not-found');
    return;
  }

  const phrases = extractSearchPhrase(snippet);
  if (phrases.length === 0) {
    row.classList.add('qa-row-not-found');
    rowState.set(key, 'not-found');
    return;
  }

  rowState.set(key, 'searching');
  row.classList.add('qa-row-searching');
  row.classList.remove('qa-row-not-found');

  let found = false;
  for (const query of phrases) {
    const result = await doFind(query);
    if (result.found) {
      found = true;
      break;
    }
  }

  row.classList.remove('qa-row-searching');

  if (!found) {
    rowState.set(key, 'not-found');
    row.classList.add('qa-row-not-found');
    row.setAttribute('title', 'Source text not located in PDF');
    clearHighlight();
    console.log('QA: not found for row index', row.dataset.index);
  } else {
    rowState.set(key, 'found');
  }
}

function doFind(query) {
  return new Promise((resolve) => {
    findResolve = resolve;
    const timeout = setTimeout(() => {
      if (findResolve === resolve) {
        findResolve = null;
        resolve({ found: false });
      }
    }, 3000);

    const origResolve = findResolve;
    findResolve = (result) => {
      clearTimeout(timeout);
      resolve(result);
    };

    findController.executeCommand('find', {
      query: query,
      highlightAll: true,
      caseSensitive: false,
      entireWord: false,
      findPrevious: false,
    });
  });
}

function clearHighlight() {
  if (!findController) return;
  findController.executeCommand('find', {
    query: '',
    highlightAll: false,
  });
}
