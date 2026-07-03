/* Exclusives PH — hero background animation
 * ------------------------------------------------------------------
 * A dark "sea" of gold contour lines that gently swells and recedes to
 * the horizon, with slow-drifting light motes above it. Renders into
 * #hero-canvas behind the page content.
 *
 * Uses the global THREE loaded by the three.js r128 <script> that sits
 * above this file in index.html. No module imports, no build step.
 *
 * Safe to include even if it can't run: if THREE is missing, the canvas
 * is absent, or WebGL is unavailable, it quietly does nothing and the
 * CSS gradient background shows through instead.
 */
(function () {
    'use strict';
  
    var canvas = document.getElementById('hero-canvas');
    if (!canvas || typeof THREE === 'undefined') return;
  
    var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  
    // Brand palette (matches tailwind.config in index.html)
    var GOLD = 0xF5C518;
    var AMBER = 0xFFD166;
    var ICE = 0xF2EADD;
    var OBSIDIAN = 0x0A1A24;
  
    var renderer, scene, camera, water, basePositions, motes;
    var raf = null;
    var start = (typeof performance !== 'undefined' ? performance.now() : Date.now());
  
    function size() {
      // The canvas is stretched by CSS (w-full h-full); read its rendered box.
      var w = canvas.clientWidth || window.innerWidth;
      var h = canvas.clientHeight || window.innerHeight;
      return { w: w, h: h };
    }
  
    try {
      renderer = new THREE.WebGLRenderer({ canvas: canvas, alpha: true, antialias: true });
    } catch (e) {
      return; // no WebGL — let the CSS background stand in
    }
  
    var dim = size();
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(dim.w, dim.h, false); // false: don't touch the CSS-driven canvas style
  
    scene = new THREE.Scene();
    // Fog fades the far edge of the sea into the obsidian night.
    scene.fog = new THREE.Fog(OBSIDIAN, 18, 62);
  
    camera = new THREE.PerspectiveCamera(60, dim.w / dim.h, 0.1, 200);
    camera.position.set(0, 7, 24);
    camera.lookAt(0, 0, -12);
  
    // --- The sea: a wide plane rendered as glowing gold wireframe contours ---
    var SEG_X = 64;   // resolution across
    var SEG_Y = 64;   // resolution in depth
    var geo = new THREE.PlaneGeometry(120, 120, SEG_X, SEG_Y);
  
    var waterMat = new THREE.MeshBasicMaterial({
      color: GOLD,
      wireframe: true,
      transparent: true,
      opacity: 0.14,
    });
  
    water = new THREE.Mesh(geo, waterMat);
    water.rotation.x = -Math.PI / 2;   // lay it flat (local +z becomes world up)
    water.position.y = -2;
    scene.add(water);
  
    // Keep a copy of the flat vertex positions to compute waves from.
    basePositions = geo.attributes.position.array.slice(0);
  
    // --- Light motes: slow gold/ice points drifting upward like embers -------
    var MOTE_COUNT = 260;
    var mGeo = new THREE.BufferGeometry();
    var mPos = new Float32Array(MOTE_COUNT * 3);
    var mVel = new Float32Array(MOTE_COUNT);       // upward speed per mote
    var mPhase = new Float32Array(MOTE_COUNT);     // for horizontal sway
  
    for (var i = 0; i < MOTE_COUNT; i++) {
      mPos[i * 3 + 0] = (Math.random() - 0.5) * 90;      // x
      mPos[i * 3 + 1] = Math.random() * 26 - 2;          // y
      mPos[i * 3 + 2] = -Math.random() * 55;             // z (into the scene)
      mVel[i] = 0.15 + Math.random() * 0.5;
      mPhase[i] = Math.random() * Math.PI * 2;
    }
    mGeo.setAttribute('position', new THREE.BufferAttribute(mPos, 3));
  
    var moteMat = new THREE.PointsMaterial({
      color: AMBER,
      size: 0.28,
      transparent: true,
      opacity: 0.55,
      depthWrite: false,
      blending: THREE.AdditiveBlending,
      sizeAttenuation: true,
    });
    motes = new THREE.Points(mGeo, moteMat);
    scene.add(motes);
  
    // --- Wave displacement ---------------------------------------------------
    function shapeWater(t) {
      var pos = geo.attributes.position.array;
      for (var k = 0; k < pos.length; k += 3) {
        var x = basePositions[k];
        var y = basePositions[k + 1];
        // Layered sines give a gentle, non-repetitive swell. Displace local z
        // (world "up" after the -90deg rotation).
        var z =
          Math.sin(x * 0.12 + t * 0.6) * 1.15 +
          Math.cos(y * 0.15 + t * 0.45) * 0.9 +
          Math.sin((x + y) * 0.08 + t * 0.9) * 0.5;
        pos[k + 2] = z;
      }
      geo.attributes.position.needsUpdate = true;
    }
  
    function driftMotes(t, dt) {
      var arr = mGeo.attributes.position.array;
      for (var j = 0; j < MOTE_COUNT; j++) {
        var iy = j * 3 + 1;
        arr[iy] += mVel[j] * dt;                                  // rise
        arr[j * 3] += Math.sin(t * 0.5 + mPhase[j]) * 0.01;       // faint sway
        if (arr[iy] > 26) {                                       // recycle at top
          arr[iy] = -3;
          arr[j * 3] = (Math.random() - 0.5) * 90;
          arr[j * 3 + 2] = -Math.random() * 55;
        }
      }
      mGeo.attributes.position.needsUpdate = true;
    }
  
    // --- Loop ----------------------------------------------------------------
    var lastT = 0;
    function frame(now) {
      var t = (now - start) / 1000;
      var dt = Math.min(0.05, t - lastT || 0.016);
      lastT = t;
  
      shapeWater(t);
      driftMotes(t, dt);
  
      // Very slight camera breathing for life.
      camera.position.y = 7 + Math.sin(t * 0.3) * 0.4;
      camera.lookAt(0, 0, -12);
  
      renderer.render(scene, camera);
      raf = requestAnimationFrame(frame);
    }
  
    function renderStaticFrame() {
      shapeWater(0);
      renderer.render(scene, camera);
    }
  
    // --- Resize --------------------------------------------------------------
    function onResize() {
      var d = size();
      renderer.setSize(d.w, d.h, false);
      camera.aspect = d.w / d.h;
      camera.updateProjectionMatrix();
      if (reduceMotion) renderStaticFrame();
    }
    window.addEventListener('resize', onResize, { passive: true });
  
    // Pause the loop when the tab is hidden (saves battery / GPU).
    document.addEventListener('visibilitychange', function () {
      if (document.hidden) {
        if (raf) { cancelAnimationFrame(raf); raf = null; }
      } else if (!reduceMotion && raf === null) {
        lastT = 0;
        raf = requestAnimationFrame(frame);
      }
    });
  
    // --- Go ------------------------------------------------------------------
    if (reduceMotion) {
      renderStaticFrame(); // honor reduced-motion: one calm frame, no animation
    } else {
      raf = requestAnimationFrame(frame);
    }
  })();