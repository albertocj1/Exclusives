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
    let pendingPayload = null;   // form data awaiting confirmation (not yet sent)
    let currentBooking = null;   // the pending booking returned by POST /api/bookings
  
    const pkgSel = $('package');
    const guestsSel = $('guests');
  
    // --------------------------------------------------------------------------
    // 1) Live estimated total on the RSVP form
    // --------------------------------------------------------------------------
    // Grey out guest counts above the selected package's max, and clamp the
    // current selection down if it's now too high. Mirrors the backend's
    // per-package max_guests so a valid form can't produce a 422.
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
  
    // Step A: form submit -> review details in the confirmation modal (no API call yet)
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
        guests: parseInt(guestsSel ? guestsSel.value : '1', 10) || 1,
        accept_terms: !!(terms && terms.checked),
      };
  
      // fill the confirmation modal from what they typed
      setText('confirm-name', pendingPayload.full_name);
      setText('confirm-email', pendingPayload.email);
      setText('confirm-package', pendingPayload.package);
      setText('confirm-guests', pendingPayload.guests);
      setText('confirm-total', peso(computeTotal(pendingPayload.package, pendingPayload.guests)));
      // optional: show referrer in the confirmation modal if that row exists
      setReferrerRow(pendingPayload.referrer);
  
      openModal($('confirm-modal'));
    });
  
    // Show/hide an optional "Referred by" row in the confirm modal. No-op unless
    // index.html has  #confirm-referrer  (and optionally #confirm-referrer-row).
    function setReferrerRow(referrer) {
      const valEl = $('confirm-referrer');
      if (!valEl) return; // row not present in the HTML — nothing to do
      const rowEl = $('confirm-referrer-row') || valEl.closest('div');
      if (referrer) {
        valEl.textContent = referrer;
        if (rowEl) rowEl.classList.remove('hidden');
      } else if (rowEl) {
        rowEl.classList.add('hidden');
      }
    }
  
    // Step B: "Back to edit" -> close, form stays filled as-is
    const editBtn = $('edit-booking');
    editBtn && editBtn.addEventListener('click', () => closeModal($('confirm-modal')));
  
    // Step C: "Confirm request" -> create the pending booking, then show the submitted modal
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
        if (!res.ok) {
          throw new Error(extractError(data) || 'Could not submit your request. Please try again.');
        }
  
        currentBooking = data;
  
        // fill the "RSVP submitted" modal
        setText('submitted-name', data.full_name);
        setText('submitted-package', data.package);
        setText('submitted-guests', data.guests);
        setText('submitted-total', peso(data.total_amount));
  
        closeModal($('confirm-modal'));
        openModal($('submitted-modal'));
      } catch (err) {
        alert(err.message);
      } finally {
        setBusy(confirmBtn, false, originalLabel || 'Confirm request');
      }
    });
  
    // Step D: "Continue to payment" -> create a PayMongo checkout session and
    // redirect the browser to PayMongo's hosted page (GCash / Maya / card).
    const continueBtn = $('continue-to-payment');
    continueBtn && continueBtn.addEventListener('click', () => {
      if (currentBooking) startCheckout(currentBooking.id, continueBtn);
    });
  
    // "I'll pay later" -> close + reset the form for the next guest
    const payLaterBtn = $('close-submitted');
    payLaterBtn && payLaterBtn.addEventListener('click', () => {
      closeModal($('submitted-modal'));
      if (form) form.reset();
      updateEstimate();
      loadAvailability();
    });
  
    async function startCheckout(bookingId, btn) {
      const originalLabel = btn ? btn.textContent : '';
      setBusy(btn, true, 'Redirecting to payment\u2026');
      try {
        const res = await fetch(`${API_BASE}/api/bookings/${bookingId}/checkout`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: '{}',
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok || !data.checkout_url) {
          throw new Error(extractError(data) || 'Could not start payment. Please try again.');
        }
        window.location.href = data.checkout_url; // hand off to PayMongo
      } catch (err) {
        alert(err.message);
        setBusy(btn, false, originalLabel || 'Continue to payment');
      }
    }
  
    // --------------------------------------------------------------------------
    // 5) Returning from PayMongo -> verify payment, then show the ticket
    // --------------------------------------------------------------------------
    // PayMongo redirects back to  index.html?booking_id=...&paid=1  (or &cancelled=1).
    async function handlePaymentReturn() {
      const params = new URLSearchParams(window.location.search);
      const bookingId = params.get('booking_id');
      if (!bookingId) return;
  
      // Clean the URL so a refresh doesn't re-trigger this.
      const cleanUrl = window.location.pathname + window.location.hash;
  
      if (params.get('cancelled')) {
        history.replaceState({}, '', cleanUrl);
        alert('Payment was cancelled. Your spot is still held — you can pay again from the confirmation email.');
        return;
      }
      if (!params.get('paid')) return;
  
      // PayMongo may take a moment to mark the session paid; retry a few times.
      for (let attempt = 0; attempt < 5; attempt++) {
        try {
          const res = await fetch(`${API_BASE}/api/bookings/${bookingId}/verify`);
          const data = await res.json().catch(() => ({}));
          if (res.ok && data.status === 'confirmed') {
            populateReceipt(data);
            openModal($('receipt-modal'));
            loadAvailability();
            history.replaceState({}, '', cleanUrl);
            return;
          }
        } catch (_) { /* transient — retry */ }
        await new Promise((r) => setTimeout(r, 1500));
      }
  
      history.replaceState({}, '', cleanUrl);
      alert('We could not confirm your payment yet. If you completed it, please contact us with your name and email.');
    }
    handlePaymentReturn();
  
    function populateReceipt(b) {
      setText('ticket-name', b.full_name);
      setText('ticket-package', b.package);
      setText('ticket-guests', b.guests);
      setText('ticket-code', b.ticket_code || 'EXC-PENDING');
    }
  
    // --------------------------------------------------------------------------
    // 7) Modal close handlers
    // --------------------------------------------------------------------------
    const confirmModalEl = $('confirm-modal');
    const submittedModalEl = $('submitted-modal');
    const receiptModal = $('receipt-modal');
  
    const closeConfirm = $('close-confirm');
    closeConfirm && closeConfirm.addEventListener('click', () => closeModal(confirmModalEl));
  
    const closeReceipt = $('close-receipt');
    closeReceipt && closeReceipt.addEventListener('click', () => {
      closeModal(receiptModal);
      if (form) form.reset();
      updateEstimate();
    });
  
    // click on the dimmed backdrop closes that modal
    [confirmModalEl, submittedModalEl, receiptModal].forEach((m) => {
      m && m.addEventListener('click', (e) => { if (e.target === m) closeModal(m); });
    });
  
    // Esc closes whichever booking modal is open
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        closeModal(confirmModalEl);
        closeModal(submittedModalEl);
        closeModal(receiptModal);
      }
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