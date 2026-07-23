document.addEventListener('DOMContentLoaded', () => {
    'use strict';
  
    const API_BASE = (window.API_BASE || 'https://exclusivesph.onrender.com').replace(/\/+$/, '');
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  
    const EXTRA_HEAD_FEE = 2500;
    const PACKAGES = {
      'Entrance Fee':        { price: 2500,  per: 'person', basePax: 1, maxGuests: 12, defaultGuests: 1, extraHead: false },
      '6-Pax Bottle Bundle': { price: 15000, per: 'bundle', basePax: 6, maxGuests: 6,  defaultGuests: 6, extraHead: false },
      'Standing Table':      { price: 8000,  per: 'table',  basePax: 4, maxGuests: 4,  defaultGuests: 4, extraHead: false },
      'Indoor Couch':        { price: 15000, per: 'table',  basePax: 6, maxGuests: 12, defaultGuests: 6, extraHead: true },
      'Outdoor Couch':       { price: 15000, per: 'table',  basePax: 6, maxGuests: 12, defaultGuests: 6, extraHead: true },
      'SVIP Couch':          { price: 20000, per: 'table',  basePax: 8, maxGuests: 14, defaultGuests: 8, extraHead: true },
    };

    // Which spots belong to which package + friendly display names.
    const PACKAGE_SPOTS = {
      'Standing Table': ['DT1','DT2'],
      'Indoor Couch':   ['LC4','LC7'],
      'Outdoor Couch':  ['DC1','DC2'],
      'SVIP Couch':     ['LC1','LC2','LC3','LC5','LC6'],
    };
    const SPOT_NAMES = {
      LC1:'SVIP 1', LC2:'SVIP 2', LC3:'SVIP 3', LC5:'SVIP 4', LC6:'SVIP 5',
      LC4:'VIP 1',  LC7:'VIP 2',  DC1:'VIP 3',  DC2:'VIP 4',
      DT1:'Table 1', DT2:'Table 2',
    };
    const spotName = (id) => SPOT_NAMES[id] || id;

    const peso = (n) => '\u20B1' + Number(n || 0).toLocaleString('en-PH');
    const computeTotal = (pkg, guests) => {
      const cfg = PACKAGES[pkg];
      if (!cfg) return 0;
      if (cfg.per === 'person') return cfg.price * guests;
      let total = cfg.price;
      if (cfg.extraHead) total += Math.max(0, guests - cfg.basePax) * EXTRA_HEAD_FEE;
      return total;
    };
  
    const $ = (id) => document.getElementById(id);
    const setText = (id, val) => { const el = $(id); if (el) el.textContent = val; };
    const getVal = (id) => { const el = $(id); return el ? el.value.trim() : ''; };
    function setBusy(btn, busy, label) {
      if (!btn) return;
      btn.disabled = busy;
      if (label != null) btn.textContent = label;
      btn.style.opacity = busy ? '0.6' : '1';
      btn.style.cursor = busy ? 'not-allowed' : 'pointer';
    }

    // --- Fetch with timeout + retry (exponential-ish backoff). ---
    function fetchWithTimeout(url, opts, timeoutMs) {
      const controller = new AbortController();
      const id = setTimeout(() => controller.abort(), timeoutMs);
      return fetch(url, Object.assign({}, opts, { signal: controller.signal }))
        .finally(() => clearTimeout(id));
    }

    async function fetchJsonWithRetry(url, { attempts = 4, timeoutMs = 8000, baseDelayMs = 900 } = {}) {
      let lastErr;
      for (let i = 0; i < attempts; i++) {
        try {
          const res = await fetchWithTimeout(url, {}, timeoutMs);
          if (!res.ok) throw new Error('HTTP ' + res.status);
          return await res.json();
        } catch (err) {
          lastErr = err;
          if (i < attempts - 1) {
            const delay = baseDelayMs * Math.pow(1.6, i);
            await new Promise((r) => setTimeout(r, delay));
          }
        }
      }
      throw lastErr;
    }
  
    const openModal = (el) => { if (!el) return; el.classList.remove('hidden'); document.body.classList.add('modal-active'); };
    const closeModal = (el) => { if (!el) return; el.classList.add('hidden'); document.body.classList.remove('modal-active'); };
  
    let pendingPayload = null;   
    let currentBooking = null;   
    let allTables = [];
    let tablesLoadFailed = false;
  
    const pkgSel = $('package');
    const tablePrefSel = $('table-pref');
    const guestsSel = $('guests');

    // --- Submit button + sold-out note ---
    const form = $('rsvp-form');
    const submitBtn = form ? form.querySelector('button[type="submit"]') : null;
    const SUBMIT_DEFAULT_LABEL = submitBtn ? submitBtn.textContent.trim() : 'Apply & continue to payment';
    let soldOutNote = null;

    function ensureSoldOutNote() {
      if (soldOutNote) return soldOutNote;
      soldOutNote = document.createElement('div');
      soldOutNote.id = 'sold-out-note';
      soldOutNote.style.cssText =
        'display:none; margin-top:4px; padding:14px 16px; border-radius:14px;' +
        'background:rgba(248,113,113,0.08); border:1px solid rgba(248,113,113,0.35);' +
        "color:#fca5a5; font-family:'Space Mono', monospace; font-size:12px;" +
        'letter-spacing:0.5px; text-align:center; line-height:1.5;';
      soldOutNote.textContent = 'All tables in this category are fully reserved. Please pick another package.';
      soldOutNote.setAttribute('role', 'status');
      if (submitBtn && submitBtn.parentElement) {
        submitBtn.parentElement.insertBefore(soldOutNote, submitBtn);
      } else if (form) {
        form.appendChild(soldOutNote);
      }
      return soldOutNote;
    }

    let eventSoldOut = false;
    let spotsLeft = null;

    function isPackageSoldOut(pkgName) {
      if (eventSoldOut) return true;
      const cfg = PACKAGES[pkgName];
      if (!cfg || cfg.per !== 'table') return false;
      const relevant = allTables.filter((t) => t.package === pkgName);
      if (relevant.length === 0) return false;
      return relevant.every((t) => !t.is_available);
    }

    function setFormSoldOut(isSoldOut) {
      const note = ensureSoldOutNote();
      note.style.display = isSoldOut ? 'block' : 'none';
      note.textContent = eventSoldOut
        ? 'The guestlist is full. No further bookings can be accepted.'
        : 'All tables in this category are fully reserved. Please pick another package.';
      if (!submitBtn) return;
      if (isSoldOut) {
        submitBtn.disabled = true;
        submitBtn.style.opacity = '0.5';
        submitBtn.style.cursor = 'not-allowed';
        submitBtn.textContent = eventSoldOut ? 'Guestlist full' : 'Sold out';
      } else {
        submitBtn.disabled = false;
        submitBtn.style.opacity = '1';
        submitBtn.style.cursor = 'pointer';
        submitBtn.textContent = SUBMIT_DEFAULT_LABEL;
      }
    }

    function updateCardSoldOut() {
      document.querySelectorAll('.select-package-btn').forEach((btn) => {
        const pkg = btn.getAttribute('data-package');
        const soldOut = isPackageSoldOut(pkg);
        const card = btn.closest('[data-reveal]') || btn.closest('div');
        if (soldOut) {
          btn.textContent = eventSoldOut ? 'Guestlist Full' : 'Sold Out';
          btn.style.opacity = '0.5';
          btn.style.cursor = 'not-allowed';
          btn.setAttribute('data-soldout', '1');
        } else {
          if (btn.getAttribute('data-soldout') === '1') {
            btn.textContent = 'Select';
            btn.style.opacity = '';
            btn.style.cursor = '';
            btn.removeAttribute('data-soldout');
          }
        }
      });
    }
  
    function applyDefaultGuests() {
      if (!pkgSel || !guestsSel) return;
      const cfg = PACKAGES[pkgSel.value];
      if (!cfg) return;
      const desired = cfg.defaultGuests || 1;
      const hasOption = Array.prototype.some.call(
        guestsSel.options, (o) => parseInt(o.value, 10) === desired
      );
      if (hasOption) guestsSel.value = String(desired);
    }

    function applyGuestLimit() {
      if (!pkgSel || !guestsSel) return;
      const cfg = PACKAGES[pkgSel.value];
      const max = cfg ? cfg.maxGuests : 8;
      Array.prototype.forEach.call(guestsSel.options, (opt) => {
        opt.disabled = parseInt(opt.value, 10) > max;
      });
      if (parseInt(guestsSel.value, 10) > max) guestsSel.value = String(max);
    }
  
    function updateEstimate() {
      if (!pkgSel || !guestsSel) return;
      applyGuestLimit();
      const total = computeTotal(pkgSel.value, parseInt(guestsSel.value, 10) || 1);
      setText('estimated-total', peso(total));
    }

    const guestNamesWrap = $('guest-names');

    function currentGuestNames() {
      if (!guestNamesWrap) return [];
      return Array.prototype.map.call(
        guestNamesWrap.querySelectorAll('input.guest-name-input'),
        function (el) { return el.value.trim(); }
      );
    }

    function renderGuestNameFields() {
      if (!guestNamesWrap || !guestsSel) return;
      const n = parseInt(guestsSel.value, 10) || 1;
      const existing = currentGuestNames();
      guestNamesWrap.innerHTML = '';
      for (let i = 0; i < n; i++) {
        const wrap = document.createElement('div');
        wrap.className = 'relative';
        const input = document.createElement('input');
        input.type = 'text';
        input.required = true;
        input.className = 'guest-name-input w-full bg-brand-slate border border-white/10 rounded-xl focus:border-brand-gold text-brand-ice font-light text-sm px-4 py-3 outline-none transition-colors duration-300';
        input.placeholder = (i === 0) ? 'Guest 1 (lead booker)' : ('Guest ' + (i + 1) + ' full name');
        if (existing[i]) input.value = existing[i];
        else if (i === 0) { const fn = $('fullname'); if (fn) input.value = fn.value.trim(); }
        wrap.appendChild(input);
        guestNamesWrap.appendChild(wrap);
      }
    }

    (function wireBookerSync() {
      const fn = $('fullname');
      if (!fn || !guestNamesWrap) return;
      fn.addEventListener('input', function () {
        const first = guestNamesWrap.querySelector('input.guest-name-input');
        if (first && (first.value.trim() === '' || first.dataset.autofill === '1')) {
          first.value = fn.value.trim();
          first.dataset.autofill = '1';
        }
      });
      guestNamesWrap.addEventListener('input', function (e) {
        if (e.target && e.target.classList.contains('guest-name-input')) {
          e.target.dataset.autofill = '';
        }
      });
    })();
    
    function updateTableDropdown() {
        if (!pkgSel || !tablePrefSel) return;
        const selectedPkg = pkgSel.value;
        tablePrefSel.innerHTML = '';
        
        // Lock the dropdown if GA or an entrance bundle is selected
        if (selectedPkg === 'Entrance Fee' || PACKAGES[selectedPkg]?.per === 'bundle') {
            const opt = document.createElement('option');
            opt.value = 'None';
            opt.textContent = 'None / Solo Entry';
            tablePrefSel.appendChild(opt);
            tablePrefSel.disabled = true;
            setFormSoldOut(false);   // entrance bundles are never table-sold-out
            return;
        }

        const relevantTables = allTables.filter(t => t.package === selectedPkg);
        tablePrefSel.disabled = false;

        if (relevantTables.length === 0) {
            const opt = document.createElement('option');
            opt.value = '';
            opt.textContent = tablesLoadFailed
                ? 'Could not load tables — tap to retry'
                : 'Loading tables\u2026';
            tablePrefSel.appendChild(opt);
            setFormSoldOut(false);
            return;
        }

        const isOutdoor = (id) => /^DC\d+/i.test(String(id));

        let hasAvailable = false;
        const buildOption = (t) => {
            const opt = document.createElement('option');
            opt.value = t.id;
            opt.textContent = `${spotName(t.id)} ${!t.is_available ? '(Reserved)' : ''}`;
            if (!t.is_available) {
                opt.disabled = true;
            } else if (!hasAvailable) {
                opt.selected = true;
                hasAvailable = true;
            }
            return opt;
        };

        const indoorTables  = relevantTables.filter(t => !isOutdoor(t.id));
        const outdoorTables = relevantTables.filter(t =>  isOutdoor(t.id));

        if (indoorTables.length && outdoorTables.length) {
            const gIn = document.createElement('optgroup');
            gIn.label = 'Indoor';
            indoorTables.forEach(t => gIn.appendChild(buildOption(t)));
            tablePrefSel.appendChild(gIn);

            const gOut = document.createElement('optgroup');
            gOut.label = 'Outdoor';
            outdoorTables.forEach(t => gOut.appendChild(buildOption(t)));
            tablePrefSel.appendChild(gOut);
        } else {
            relevantTables.forEach(t => tablePrefSel.appendChild(buildOption(t)));
        }

        if (!hasAvailable) {
            tablePrefSel.innerHTML = '';
            const opt = document.createElement('option');
            opt.value = '';
            opt.textContent = 'All tables reserved — Sold out';
            opt.disabled = true;
            opt.selected = true;
            tablePrefSel.appendChild(opt);
            tablePrefSel.disabled = true;
            setFormSoldOut(true);
        } else {
            setFormSoldOut(false);
        }
    }

    tablePrefSel && tablePrefSel.addEventListener('mousedown', () => {
      if (tablesLoadFailed && pkgSel && PACKAGES[pkgSel.value]?.per === 'table') {
        loadTables();
      }
    });

    pkgSel && pkgSel.addEventListener('change', () => {
        applyDefaultGuests();
        updateTableDropdown();
        updateEstimate();
        renderGuestNameFields();
        if (tablesLoadFailed && PACKAGES[pkgSel.value]?.per === 'table') {
          loadTables();
        }
    });
    guestsSel && guestsSel.addEventListener('change', function(){ updateEstimate(); renderGuestNameFields(); });
  
    const PUBLIC_CAPACITY = 150;
    const LOW_STOCK_AT = 100;

    function scarcityStrip() {
      const fill = $('scarcity-fill');
      return fill ? fill.closest('section') : null;
    }

    function paintScarcity(state) {
      const strip = scarcityStrip();
      if (!strip) return;
      const heading = strip.querySelector('.font-mono.uppercase');
      const dot = strip.querySelector('span.rounded-full.bg-brand-gold, span.w-2.h-2');
      const fill = $('scarcity-fill');

      if (state === 'full') {
        if (heading) heading.textContent = 'Guestlist full';
        if (dot) { dot.classList.remove('animate-pulse'); dot.style.background = '#8AA0AD'; }
        if (fill) fill.style.background = 'linear-gradient(90deg, #8AA0AD, #F2EADD)';
      } else if (state === 'low') {
        if (heading) heading.textContent = 'Only a few spots left';
        if (dot) { dot.classList.add('animate-pulse'); dot.style.background = ''; }
        if (fill) fill.style.background = '';
      } else {
        if (heading) heading.textContent = 'Guestlist filling';
        if (dot) { dot.classList.add('animate-pulse'); dot.style.background = ''; }
        if (fill) fill.style.background = '';
      }
    }

    function paintScarcitySoldOut() { paintScarcity('full'); }

    async function loadAvailability() {
      const spotsEl = $('spots-left');
      const capEl = $('spots-capacity');
      const fill = $('scarcity-fill');
      if (capEl) capEl.textContent = PUBLIC_CAPACITY;
      try {
        const data = await fetchJsonWithRetry(`${API_BASE}/api/availability`);

        const capacity = data.capacity != null ? data.capacity : PUBLIC_CAPACITY;
        const taken = data.taken != null ? data.taken : 0;
        spotsLeft = data.spots_left != null ? data.spots_left : Math.max(0, capacity - taken);
        eventSoldOut = data.sold_out === true || spotsLeft <= 0;

        if (capEl) capEl.textContent = capacity;
        if (spotsEl) spotsEl.textContent = spotsLeft;
        if (fill) {
          const pct = Math.min(100, Math.max(0, (taken / capacity) * 100));
          if (reduceMotion) fill.style.width = pct + '%';
          else requestAnimationFrame(() => { fill.style.width = pct + '%'; });
        }

        if (eventSoldOut) {
          paintScarcity('full');
          setFormSoldOut(true);
          lockGuestSelectorsAtZero();
        } else if (taken >= LOW_STOCK_AT) {
          paintScarcity('low');
          lockGuestSelectorsAtZero();
        } else {
          paintScarcity('filling');
        }
        updateCardSoldOut();
      } catch (_) {
        // Backend unreachable is NOT sold out
      }
    }

    function lockGuestSelectorsAtZero() {
      if (!guestsSel || spotsLeft == null) return;
      Array.prototype.forEach.call(guestsSel.options, (o) => {
        o.disabled = parseInt(o.value, 10) > spotsLeft;
      });
    }

    let tablesLoadToken = 0;

    async function loadTables() {
      const myToken = ++tablesLoadToken;
      tablesLoadFailed = false;
      if (pkgSel && PACKAGES[pkgSel.value]?.per === 'table') updateTableDropdown();
      try {
        const data = await fetchJsonWithRetry(`${API_BASE}/api/tables/availability`);
        if (myToken !== tablesLoadToken) return;
        allTables = data.tables || [];
        tablesLoadFailed = false;
        updateTableDropdown();
        updateEstimate();
        updateCardSoldOut();
      } catch (e) {
        if (myToken !== tablesLoadToken) return;
        console.error('Failed to load table map after retries', e);
        tablesLoadFailed = true;
        updateTableDropdown();
      }
    }

    loadAvailability();
    loadTables();
    renderGuestNameFields();
  
    document.querySelectorAll('.select-package-btn').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        const pkg = btn.getAttribute('data-package');
        if (isPackageSoldOut(pkg)) {
          e.preventDefault();
          alert('Sorry, all tables in this category are sold out. Please choose another package.');
          return;
        }
        if (pkg && pkgSel) {
          pkgSel.value = pkg;
          applyDefaultGuests();
          updateTableDropdown(); 
          updateEstimate();
          renderGuestNameFields();
          if (tablesLoadFailed && PACKAGES[pkg]?.per === 'table') loadTables();
        }
      });
    });
  
    form && form.addEventListener('submit', (e) => {
      e.preventDefault();
      const terms = $('verify-terms');
      if (terms && !terms.checked) {
        alert('Please confirm all guests are 18+ and agree to the safety directives.');
        return;
      }

      const names = currentGuestNames();
      const guestCount = parseInt(guestsSel ? guestsSel.value : '1', 10) || 1;
      if (names.length < guestCount || names.some(function (nm) { return !nm; })) {
        alert('Please enter a name for all ' + guestCount + ' guest' + (guestCount > 1 ? 's' : '') + '. The Manila Yacht Club requires every guest to be named.');
        return;
      }

      const selectedPkg = pkgSel ? pkgSel.value : '';

      if (eventSoldOut) {
        alert('The guestlist is full — no spots remain. Follow @exclusivesph for the next event.');
        setFormSoldOut(true);
        return;
      }
      if (spotsLeft != null && guestCount > spotsLeft) {
        alert('Only ' + spotsLeft + ' spot' + (spotsLeft === 1 ? '' : 's') + ' left. Please reduce your guest count.');
        return;
      }

      if (isPackageSoldOut(selectedPkg)) {
        alert('Sorry, all tables in this category are sold out. Please choose another package.');
        setFormSoldOut(true);
        return;
      }

      const cfg = PACKAGES[selectedPkg];
      const chosenTable = tablePrefSel && tablePrefSel.value !== 'None' ? tablePrefSel.value : null;

      if (cfg && cfg.per === 'table') {
        if (!chosenTable) {
          if (tablesLoadFailed) {
            alert('We could not load the table map. Please check your connection and try again.');
            loadTables();
          } else {
            alert('Please select an available table for this package.');
          }
          return;
        }
      }
  
      pendingPayload = {
        full_name: getVal('fullname'),
        email: getVal('email'),
        phone: getVal('phone'),
        instagram: getVal('instagram') || null,
        referrer: getVal('referrer') || null,
        package: selectedPkg,
        table_id: chosenTable,
        guests: parseInt(guestsSel ? guestsSel.value : '1', 10) || 1,
        guest_names: names,
        accept_terms: true,
      };
  
      setText('confirm-name', pendingPayload.full_name);
      setText('confirm-email', pendingPayload.email);
      setText('confirm-package', pendingPayload.package);
      setText('confirm-guests', pendingPayload.guests);
      setText('confirm-table', pendingPayload.table_id || 'None');
      setText('confirm-total', peso(computeTotal(pendingPayload.package, pendingPayload.guests)));
      
      const refRow = $('confirm-referrer-row');
      const refVal = $('confirm-referrer');
      if (refRow && refVal) {
          if (pendingPayload.referrer) {
              refVal.textContent = pendingPayload.referrer;
              refRow.style.display = 'flex';
          } else {
              refRow.style.display = 'none';
          }
      }
  
      openModal($('confirm-modal'));
    });
  
    const editBtn = $('edit-booking');
    editBtn && editBtn.addEventListener('click', () => closeModal($('confirm-modal')));
  
    const confirmBtn = $('confirm-booking');
    confirmBtn && confirmBtn.addEventListener('click', async () => {
      if (!pendingPayload) return;
      const originalLabel = confirmBtn.textContent;
      setBusy(confirmBtn, true, 'Submitting\u2026');
      try {
        const res = await fetch(`${API_BASE}/api/bookings`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(pendingPayload),
        });
        const data = await res.json().catch(() => ({}));
        
        if (res.status === 409) {
          const msg = extractError(data) || '';
          if (/full|spot/i.test(msg)) {
            eventSoldOut = true;
            setFormSoldOut(true);
            updateCardSoldOut();
            paintScarcitySoldOut();
            loadAvailability();
            throw new Error(msg || 'The guestlist just filled up. No spots remain.');
          }
          throw new Error('Sorry, that table was just reserved by someone else! Please pick another.');
        }
        if (!res.ok) throw new Error(extractError(data) || 'Could not submit your request.');
  
        currentBooking = data;
        
        closeModal($('confirm-modal'));
        setText('payment-total-ui', peso(data.total_amount));
        openModal($('payment-modal')); 
      } catch (err) {
        alert(err.message);
        loadTables(); 
        closeModal($('confirm-modal'));
      } finally {
        setBusy(confirmBtn, false, originalLabel || 'Confirm request');
      }
    });
  
    const submitRefBtn = $('submit-reference'); 
    submitRefBtn && submitRefBtn.addEventListener('click', async () => {
      if (!currentBooking) return;
      
      const fileInput = $('receipt-upload');
      const file = fileInput ? fileInput.files[0] : null;
      if (!file) return alert('Please upload a screenshot of your transfer receipt.');

      const originalLabel = submitRefBtn.textContent;
      setBusy(submitRefBtn, true, 'Uploading\u2026');
      
      const formData = new FormData();
      formData.append('receipt', file);

      try {
        const res = await fetch(`${API_BASE}/api/bookings/${currentBooking.id}/submit-payment`, {
          method: 'POST',
          body: formData, 
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(extractError(data) || 'Could not upload receipt.');

        setText('submitted-name', currentBooking.full_name);
        setText('submitted-package', currentBooking.package);
        setText('submitted-guests', currentBooking.guests);
        setText('submitted-total', peso(currentBooking.total_amount));
        
        closeModal($('payment-modal'));
        openModal($('submitted-modal'));
      } catch (err) {
        alert(err.message);
      } finally {
        setBusy(submitRefBtn, false, originalLabel);
      }
    });

    const payLaterBtn = $('close-submitted');
    payLaterBtn && payLaterBtn.addEventListener('click', () => {
      closeModal($('submitted-modal'));
      if (form) form.reset();
      loadTables(); 
    });
  
    const confirmModalEl = $('confirm-modal');
    const paymentModalEl = $('payment-modal');
    const submittedModalEl = $('submitted-modal');
  
    const closeConfirm = $('close-confirm');
    closeConfirm && closeConfirm.addEventListener('click', () => closeModal(confirmModalEl));
    const closePayment = $('close-payment');
    closePayment && closePayment.addEventListener('click', () => closeModal(paymentModalEl));
  
    [confirmModalEl, paymentModalEl, submittedModalEl].forEach((m) => {
      m && m.addEventListener('click', (e) => { if (e.target === m) closeModal(m); });
    });
  
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        closeModal(confirmModalEl);
        closeModal(paymentModalEl);
        closeModal(submittedModalEl);
      }
    });
  
    function extractError(data) {
      if (!data) return '';
      if (typeof data.detail === 'string') return data.detail;
      if (Array.isArray(data.detail)) return data.detail.map((d) => d.msg || JSON.stringify(d)).join('; ');
      return '';
    }
});