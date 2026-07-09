document.addEventListener('DOMContentLoaded', () => {
    'use strict';
  
    const API_BASE = (window.API_BASE || 'https://exclusivesph.onrender.com').replace(/\/+$/, '');
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  
    const PACKAGES = {
      'Entrance Fee':           { price: 2500,  per: 'person', maxGuests: 8, defaultGuests: 1 },
      'Standing Table (4 pax)': { price: 8000,  per: 'table',  maxGuests: 4, defaultGuests: 4 },
      'Couch (6 pax)':          { price: 15000, per: 'table',  maxGuests: 6, defaultGuests: 6 },
      'Couch (8 pax)':          { price: 20000, per: 'table',  maxGuests: 8, defaultGuests: 8 },
    };
  
    const peso = (n) => '\u20B1' + Number(n || 0).toLocaleString('en-PH');
    const computeTotal = (pkg, guests) => {
      const cfg = PACKAGES[pkg];
      if (!cfg) return 0;
      return cfg.per === 'person' ? cfg.price * guests : cfg.price;
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
  
    const openModal = (el) => { if (!el) return; el.classList.remove('hidden'); document.body.classList.add('modal-active'); };
    const closeModal = (el) => { if (!el) return; el.classList.add('hidden'); document.body.classList.remove('modal-active'); };
  
    let pendingPayload = null;   
    let currentBooking = null;   
    let allTables = []; 
  
    const pkgSel = $('package');
    const tablePrefSel = $('table-pref');
    const guestsSel = $('guests');

    // --- Submit button + sold-out note (note is created dynamically) ---
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
      if (submitBtn && submitBtn.parentElement) {
        submitBtn.parentElement.insertBefore(soldOutNote, submitBtn);
      } else if (form) {
        form.appendChild(soldOutNote);
      }
      return soldOutNote;
    }

    // True only when the package uses tables AND every table for it is reserved.
    function isPackageSoldOut(pkgName) {
      const cfg = PACKAGES[pkgName];
      if (!cfg || cfg.per !== 'table') return false;      // per-person entry never table-sold-out
      const relevant = allTables.filter((t) => t.package === pkgName);
      if (relevant.length === 0) return false;             // tables not loaded yet / backend offline -> not "sold out"
      return relevant.every((t) => !t.is_available);       // all reserved
    }

    function setFormSoldOut(isSoldOut) {
      const note = ensureSoldOutNote();
      note.style.display = isSoldOut ? 'block' : 'none';
      if (!submitBtn) return;
      if (isSoldOut) {
        submitBtn.disabled = true;
        submitBtn.style.opacity = '0.5';
        submitBtn.style.cursor = 'not-allowed';
        submitBtn.textContent = 'Sold out';
      } else {
        submitBtn.disabled = false;
        submitBtn.style.opacity = '1';
        submitBtn.style.cursor = 'pointer';
        submitBtn.textContent = SUBMIT_DEFAULT_LABEL;
      }
    }

    // Reflect sold-out status on the package cards (Select buttons + a badge).
    function updateCardSoldOut() {
      document.querySelectorAll('.select-package-btn').forEach((btn) => {
        const pkg = btn.getAttribute('data-package');
        const soldOut = isPackageSoldOut(pkg);
        // find the card container to place/remove a badge
        const card = btn.closest('[data-reveal]') || btn.closest('div');
        if (soldOut) {
          btn.textContent = 'Sold Out';
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
  
    // Pre-fill the guest count to match the package size (e.g. Couch 6 pax -> 6).
    // Per-person "Entrance Fee" stays at whatever the guest picks (default 1).
    function applyDefaultGuests() {
      if (!pkgSel || !guestsSel) return;
      const cfg = PACKAGES[pkgSel.value];
      if (!cfg) return;
      const desired = cfg.defaultGuests || 1;
      // Only set if the option exists (it always does for 1..8)
      const hasOption = Array.prototype.some.call(
        guestsSel.options, (o) => parseInt(o.value, 10) === desired
      );
      if (hasOption) guestsSel.value = String(desired);
    }

    // --- 1. Form Estimation & Limits ---
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
    
    // --- 2. Dynamic Table Dropdown Logic ---
    function updateTableDropdown() {
        if (!pkgSel || !tablePrefSel) return;
        const selectedPkg = pkgSel.value;
        tablePrefSel.innerHTML = '';
        
        // Lock the dropdown if GA is selected
        if (selectedPkg === 'Entrance Fee') {
            const opt = document.createElement('option');
            opt.value = 'None';
            opt.textContent = 'None / Solo Entry';
            tablePrefSel.appendChild(opt);
            tablePrefSel.disabled = true;
            setFormSoldOut(false);   // entry is never table-sold-out
            return;
        }

        // Filter tables to ONLY match the selected package
        const relevantTables = allTables.filter(t => t.package === selectedPkg);
        tablePrefSel.disabled = false; // Unlock the dropdown

        if (relevantTables.length === 0) {
            const opt = document.createElement('option');
            opt.value = '';
            opt.textContent = 'Backend offline or Loading tables...';
            tablePrefSel.appendChild(opt);
            setFormSoldOut(false);   // loading/offline is not the same as sold out
            return;
        }

        let hasAvailable = false;
        relevantTables.forEach(t => {
            const opt = document.createElement('option');
            opt.value = t.id;
            opt.textContent = `${t.id} ${!t.is_available ? '(Reserved)' : ''}`;
            
            if (!t.is_available) {
                opt.disabled = true;
            } else {
                if (!hasAvailable) {
                    opt.selected = true; // Auto-select first free table
                    hasAvailable = true;
                }
            }
            tablePrefSel.appendChild(opt);
        });

        if (!hasAvailable) {
            // Every table in this category is reserved -> SOLD OUT
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

    // Trigger updates when the user clicks a dropdown
    pkgSel && pkgSel.addEventListener('change', () => {
        applyDefaultGuests();
        updateTableDropdown();
        updateEstimate();
    });
    guestsSel && guestsSel.addEventListener('change', updateEstimate);
  
    // --- 3. Backend Fetching ---
    async function loadAvailability() {
      const spotsEl = $('spots-left');
      const capEl = $('spots-capacity');
      const fill = $('scarcity-fill');
      try {
        const res = await fetch(`${API_BASE}/api/availability`);
        if (!res.ok) return;
        const data = await res.json(); 
        if (spotsEl) spotsEl.textContent = data.spots_left;
        if (capEl && data.capacity != null) capEl.textContent = data.capacity;
        if (fill && data.capacity) {
          const pct = Math.min(100, Math.max(0, (data.taken / data.capacity) * 100));
          if (reduceMotion) fill.style.width = pct + '%';
          else requestAnimationFrame(() => { fill.style.width = pct + '%'; });
        }
      } catch (_) {}
    }
    
    async function loadTables() {
      try {
        const res = await fetch(`${API_BASE}/api/tables/availability`);
        const data = await res.json();
        allTables = data.tables; 
        updateTableDropdown();   
        updateEstimate();
        updateCardSoldOut();     // reflect sold-out on the package cards
      } catch (e) {
        console.error("Failed to load map", e);
      }
    }

    // Initialize 
    loadAvailability();
    loadTables();
  
    // Package Cards "Select" Buttons (From HTML layout)
    document.querySelectorAll('.select-package-btn').forEach((btn) => {
      btn.addEventListener('click', (e) => {
        const pkg = btn.getAttribute('data-package');
        // Block selecting a sold-out package
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
        }
      });
    });
  
    // --- 4. RSVP Submit ---
    form && form.addEventListener('submit', (e) => {
      e.preventDefault();
      const terms = $('verify-terms');
      if (terms && !terms.checked) {
        alert('Please confirm all guests are 18+ and agree to the safety directives.');
        return;
      }

      const selectedPkg = pkgSel ? pkgSel.value : '';

      // HARD BLOCK: if this is a table package and everything is reserved, stop here.
      if (isPackageSoldOut(selectedPkg)) {
        alert('Sorry, all tables in this category are sold out. Please choose another package.');
        setFormSoldOut(true);
        return;
      }

      const cfg = PACKAGES[selectedPkg];
      const chosenTable = tablePrefSel && tablePrefSel.value !== 'None' ? tablePrefSel.value : null;

      // Table packages must have a real, available table selected.
      if (cfg && cfg.per === 'table') {
        if (!chosenTable) {
          alert('Please select an available table for this package.');
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
  
    // --- 5. Modal Navigation ---
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
        
        if (res.status === 409) throw new Error('Sorry, that table was just reserved by someone else! Please pick another.');
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
  
    // --- 6. Receipt Upload ---
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