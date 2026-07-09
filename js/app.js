document.addEventListener('DOMContentLoaded', () => {
    'use strict';
  
    const API_BASE = (window.API_BASE || 'https://exclusivesph.onrender.com').replace(/\/+$/, '');
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  
    const PACKAGES = {
      'Entrance Fee':           { price: 2500,  per: 'person', maxGuests: 8 },
      'Standing Table (4 pax)': { price: 8000,  per: 'table',  maxGuests: 4 },
      'Couch (6 pax)':          { price: 15000, per: 'table',  maxGuests: 6 },
      'Couch (8 pax)':          { price: 20000, per: 'table',  maxGuests: 8 },
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

        if (!hasAvailable && relevantTables.length > 0) {
            tablePrefSel.selectedIndex = 0;
        }
    }

    // Trigger updates when the user clicks a dropdown
    pkgSel && pkgSel.addEventListener('change', () => {
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
      } catch (e) {
        console.error("Failed to load map", e);
      }
    }

    // Initialize 
    loadAvailability();
    loadTables();
  
    // Package Cards "Select" Buttons (From HTML layout)
    document.querySelectorAll('.select-package-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        const pkg = btn.getAttribute('data-package');
        if (pkg && pkgSel) {
          pkgSel.value = pkg;
          updateTableDropdown(); 
          updateEstimate();
        }
      });
    });
  
    // --- 4. RSVP Submit ---
    const form = $('rsvp-form');
    form && form.addEventListener('submit', (e) => {
      e.preventDefault();
      const terms = $('verify-terms');
      if (terms && !terms.checked) {
        alert('Please confirm all guests are 18+ and agree to the safety directives.');
        return;
      }
  
      pendingPayload = {
        full_name: getVal('fullname'),
        email: getVal('email'),
        phone: getVal('phone'),
        instagram: getVal('instagram') || null,
        referrer: getVal('referrer') || null,
        package: pkgSel ? pkgSel.value : '',
        table_id: tablePrefSel && tablePrefSel.value !== 'None' ? tablePrefSel.value : null,
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