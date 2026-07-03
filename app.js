/* Exclusives PH — booking flow wired to the FastAPI backend.
 *
 * Owns:
 *   - live estimated total (mirrors backend pricing rules)
 *   - live availability / scarcity bar   (GET  /api/availability)
 *   - RSVP form submit -> create booking  (POST /api/bookings)
 *   - payment modal -> confirm + issue ticket (POST /api/bookings/{id}/pay)
 *   - receipt modal
 *
 * Countdown, nav, FAQ, lightbox, reveal-on-scroll live in the inline
 * <script> at the bottom of index.html. This file is additive to that.
 */
document.addEventListener('DOMContentLoaded', () => {
    'use strict';
  
    const API_BASE = (window.API_BASE || 'http://localhost:8000').replace(/\/+$/, '');
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  
    // --- pricing config: must match the backend's rules -----------------------
    // GA is charged per person; the two table packages are a flat rate.
    const PACKAGES = {
      'General Admission':     { price: 5000,  per: 'person' },
      'VIP Cockpit Lounge':    { price: 45000, per: 'table'  },
      'VVIP Main Cabin Table': { price: 75000, per: 'table'  },
    };
  
    const peso = (n) => '\u20B1' + Number(n || 0).toLocaleString('en-PH');
  
    const computeTotal = (pkg, guests) => {
      const cfg = PACKAGES[pkg];
      if (!cfg) return 0;
      return cfg.per === 'person' ? cfg.price * guests : cfg.price;
    };
  
    // --- small DOM helpers ----------------------------------------------------
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
  
    // --- modal helpers (modals use Tailwind's `hidden` class) -----------------
    const openModal = (el) => { if (!el) return; el.classList.remove('hidden'); document.body.classList.add('modal-active'); };
    const closeModal = (el) => { if (!el) return; el.classList.add('hidden'); document.body.classList.remove('modal-active'); };
  
    // --- shared state ---------------------------------------------------------
    let currentBooking = null;   // the pending booking returned by POST /api/bookings
    let selectedMethod = 'gcash';
  
    const pkgSel = $('package');
    const guestsSel = $('guests');
  
    // --------------------------------------------------------------------------
    // 1) Live estimated total on the RSVP form
    // --------------------------------------------------------------------------
    function updateEstimate() {
      if (!pkgSel || !guestsSel) return;
      const total = computeTotal(pkgSel.value, parseInt(guestsSel.value, 10) || 1);
      setText('estimated-total', peso(total));
    }
    pkgSel && pkgSel.addEventListener('change', updateEstimate);
    guestsSel && guestsSel.addEventListener('change', updateEstimate);
    updateEstimate();
  
    // --------------------------------------------------------------------------
    // 2) Live availability / scarcity bar
    // --------------------------------------------------------------------------
    async function loadAvailability() {
      const spotsEl = $('spots-left');
      const capEl = $('spots-capacity');
      const fill = $('scarcity-fill');
      try {
        const res = await fetch(`${API_BASE}/api/availability`);
        if (!res.ok) return;
        const data = await res.json(); // { capacity, taken, spots_left }
        if (spotsEl) spotsEl.textContent = data.spots_left;
        if (capEl && data.capacity != null) capEl.textContent = data.capacity;
        if (fill && data.capacity) {
          const pct = Math.min(100, Math.max(0, (data.taken / data.capacity) * 100));
          if (reduceMotion) fill.style.width = pct + '%';
          else requestAnimationFrame(() => { fill.style.width = pct + '%'; });
        }
      } catch (_) {
        // Backend offline: leave the placeholder values, don't break the page.
      }
    }
    loadAvailability();
  
    // --------------------------------------------------------------------------
    // 3) Package cards "Select" -> preselect in the form + jump to RSVP
    // --------------------------------------------------------------------------
    document.querySelectorAll('.select-package-btn').forEach((btn) => {
      btn.addEventListener('click', () => {
        const pkg = btn.getAttribute('data-package');
        if (pkg && pkgSel) {
          pkgSel.value = pkg;
          updateEstimate();
        }
        // the anchor href="#rsvp" already handles the scroll
      });
    });
  
    // --------------------------------------------------------------------------
    // 4) RSVP form submit -> create a pending booking
    // --------------------------------------------------------------------------
    const form = $('rsvp-form');
    form && form.addEventListener('submit', async (e) => {
      e.preventDefault();
  
      const terms = $('verify-terms');
      if (terms && !terms.checked) {
        alert('Please confirm all guests are 18+ and agree to the safety directives.');
        return;
      }
  
      const payload = {
        full_name: getVal('fullname'),
        email: getVal('email'),
        phone: getVal('phone'),
        instagram: getVal('instagram') || null,
        package: pkgSel ? pkgSel.value : '',
        guests: parseInt(guestsSel ? guestsSel.value : '1', 10) || 1,
        accept_terms: !!(terms && terms.checked),
      };
  
      const submitBtn = form.querySelector('button[type="submit"]');
      const originalLabel = submitBtn ? submitBtn.textContent : '';
      setBusy(submitBtn, true, 'Processing\u2026');
  
      try {
        const res = await fetch(`${API_BASE}/api/bookings`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          throw new Error(extractError(data) || 'Could not create your booking. Please try again.');
        }
  
        currentBooking = data;
        populatePaymentSummary(data);
  
        const payPhone = $('payment-phone');
        if (payPhone) payPhone.value = data.phone || payload.phone || '';
  
        openModal($('payment-modal'));
      } catch (err) {
        alert(err.message);
      } finally {
        setBusy(submitBtn, false, originalLabel || 'Apply & continue to payment');
      }
    });
  
    function populatePaymentSummary(b) {
      setText('summary-name', b.full_name);
      setText('summary-package', b.package);
      setText('summary-guests', b.guests);
      setText('summary-total', peso(b.total_amount));
    }
  
    // --------------------------------------------------------------------------
    // 5) Payment method toggle (GCash / Maya)
    // --------------------------------------------------------------------------
    const methodBtns = document.querySelectorAll('.payment-method-btn');
    methodBtns.forEach((btn) => {
      btn.addEventListener('click', () => {
        selectedMethod = btn.getAttribute('data-method') || 'gcash';
        methodBtns.forEach((b) => {
          const active = b === btn;
          b.classList.toggle('border-brand-gold', active);
          b.classList.toggle('bg-brand-goldDim/20', active);
          b.classList.toggle('text-brand-gold', active);
          b.classList.toggle('border-white/10', !active);
          b.classList.toggle('text-brand-muted', !active);
        });
      });
    });
  
    // --------------------------------------------------------------------------
    // 6) Pay -> confirm the booking, get the ticket code
    // --------------------------------------------------------------------------
    const payBtn = $('pay-button');
    payBtn && payBtn.addEventListener('click', async () => {
      if (!currentBooking) return;
  
      const phone = getVal('payment-phone');
      if (phone.length < 7) {
        alert('Enter the phone number registered to your ' + selectedMethod.toUpperCase() + ' account.');
        return;
      }
  
      const originalLabel = payBtn.textContent;
      setBusy(payBtn, true, 'Authorising\u2026');
  
      try {
        const res = await fetch(`${API_BASE}/api/bookings/${currentBooking.id}/pay`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ payment_method: selectedMethod, payment_phone: phone }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          throw new Error(extractError(data) || 'Payment could not be completed. Please try again.');
        }
  
        closeModal($('payment-modal'));
        populateReceipt(data);
        openModal($('receipt-modal'));
  
        // refresh the scarcity bar now that a spot is confirmed
        loadAvailability();
      } catch (err) {
        alert(err.message);
      } finally {
        setBusy(payBtn, false, originalLabel || 'Authorise & pay');
      }
    });
  
    function populateReceipt(b) {
      setText('ticket-name', b.full_name);
      setText('ticket-package', b.package);
      setText('ticket-guests', b.guests);
      setText('ticket-code', b.ticket_code || 'EXC-PENDING');
    }
  
    // --------------------------------------------------------------------------
    // 7) Modal close handlers
    // --------------------------------------------------------------------------
    const paymentModal = $('payment-modal');
    const receiptModal = $('receipt-modal');
  
    const closeBtn = $('close-modal');
    closeBtn && closeBtn.addEventListener('click', () => closeModal(paymentModal));
  
    const closeReceipt = $('close-receipt');
    closeReceipt && closeReceipt.addEventListener('click', () => {
      closeModal(receiptModal);
      if (form) form.reset();
      updateEstimate();
    });
  
    // click on the dimmed backdrop closes the modal
    [paymentModal, receiptModal].forEach((m) => {
      m && m.addEventListener('click', (e) => { if (e.target === m) closeModal(m); });
    });
  
    // Esc closes whichever booking modal is open
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { closeModal(paymentModal); closeModal(receiptModal); }
    });
  
    // --------------------------------------------------------------------------
    // helpers
    // --------------------------------------------------------------------------
    // FastAPI puts validation/HTTP errors under `detail`, which can be a string
    // or an array of {msg, loc} objects.
    function extractError(data) {
      if (!data) return '';
      if (typeof data.detail === 'string') return data.detail;
      if (Array.isArray(data.detail)) {
        return data.detail.map((d) => d.msg || JSON.stringify(d)).join('; ');
      }
      return '';
    }
  });