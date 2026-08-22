/* Ellivate — the parallax world and the reveals are CSS scroll-driven
   animations wherever the browser supports them. Everything below is a UI
   control, a low-frequency typewriter, or a fallback. No per-frame loop. */
(function () {
  'use strict';

  var root = document.documentElement;
  var supports = window.CSS && CSS.supports;
  var hasScrollTL = !!supports && CSS.supports('animation-timeline', 'scroll()');
  var hasViewTL   = !!supports && CSS.supports('animation-timeline', 'view()');

  /* ---- motion toggle ---------------------------------------------------- */
  var KEY = 'ellivate:motion';
  var toggle = document.querySelector('[data-motion-toggle]');
  var stored = null;
  try { stored = localStorage.getItem(KEY); } catch (e) {}
  var reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  root.dataset.motion = stored || (reduced ? 'off' : 'on');
  paintToggle();

  if (toggle) {
    toggle.addEventListener('click', function () {
      root.dataset.motion = root.dataset.motion === 'on' ? 'off' : 'on';
      try { localStorage.setItem(KEY, root.dataset.motion); } catch (e) {}
      paintToggle();
      root.dataset.motion === 'on' ? type.start() : type.stop(true);
    });
  }
  function paintToggle() {
    if (!toggle) return;
    var on = root.dataset.motion === 'on';
    toggle.textContent = 'Motion ' + (on ? 'on' : 'off');
    toggle.setAttribute('aria-pressed', String(on));
  }

  /* ---- the search box types itself ------------------------------------- */
  var QUESTIONS = [
    'Why did Nvidia increase CapEx?',
    'Compare Apple vs Microsoft AI spending.',
    'Show every mention of Blackwell.',
    'Why did Netflix margins improve?',
    "Show Tesla's biggest new risk factor.",
    'Compare Amazon and Shopify logistics strategy.',
    'Everything Jensen Huang said about Blackwell.'
  ];
  var IDLE = 'Search Apple, Nvidia, Tesla…';

  var type = (function () {
    var form  = document.getElementById('askform');
    var input = form && form.querySelector('[data-typewriter]');
    var out   = form && form.querySelector('.ghost-text');
    if (!form || !input || !out) return { start: function () {}, stop: function () {} };

    var qi = 0, ci = 0, timer = null, erasing = false, live = false;

    function schedule(ms) { clearTimeout(timer); timer = setTimeout(tick, ms); }

    function tick() {
      var q = QUESTIONS[qi];
      if (!erasing) {
        ci++;
        out.textContent = q.slice(0, ci);
        if (ci >= q.length) { erasing = true; return schedule(2600); }  // hold ~3s per question
        return schedule(34 + Math.random() * 40);
      }
      ci -= 3;
      if (ci <= 0) {
        ci = 0; erasing = false;
        qi = (qi + 1) % QUESTIONS.length;
        out.textContent = '';
        return schedule(260);
      }
      out.textContent = q.slice(0, ci);
      schedule(16);
    }

    function start() {
      if (live || input.value || root.dataset.motion === 'off') return;
      live = true;
      form.classList.add('typing');
      form.classList.remove('hushed');
      schedule(700);
    }

    function stop(showIdle) {
      live = false;
      clearTimeout(timer);
      form.classList.remove('typing');
      if (showIdle) {
        ci = 0; erasing = false;
        out.textContent = input.value ? '' : IDLE;
        form.classList.toggle('hushed', !!input.value);
      }
    }

    /* hand the box over the moment a human touches it */
    input.addEventListener('focus', function () { stop(true); });
    input.addEventListener('input', function () {
      stop(true);
      form.classList.toggle('hushed', !!input.value);
    });
    form.addEventListener('submit', function (e) {
      if (!input.value.trim()) { e.preventDefault(); input.focus(); }
    });

    /* don't animate off-screen or in a hidden tab */
    document.addEventListener('visibilitychange', function () {
      document.hidden ? stop(false) : start();
    });
    if ('IntersectionObserver' in window) {
      new IntersectionObserver(function (es) {
        es[0].isIntersecting ? start() : stop(false);
      }, { threshold: 0.15 }).observe(form);
    } else { start(); }

    out.textContent = IDLE;
    return { start: start, stop: stop, fill: function (text) {
      stop(true);
      input.value = text;
      form.classList.add('hushed');
      input.focus();
      input.setSelectionRange(text.length, text.length);
    } };
  })();

  /* ---- example questions load the search box --------------------------- */
  document.querySelectorAll('[data-fill]').forEach(function (el) {
    el.addEventListener('click', function () {
      var q = el.getAttribute('data-fill');
      document.getElementById('ask').scrollIntoView({ block: 'center' });
      if (type.fill) type.fill(q);
    });
  });

  /* ---- the filing reel: how far it must travel is a layout question ----
     The cited paragraph has to land in the middle of the viewer, and that
     distance depends on the rendered height of the text above it. Measured
     once (and on resize / after webfonts settle), never per frame. */
  var measureReel = function () {
    var vp = document.querySelector('.pdf-viewport');
    var tgt = document.querySelector('.pg-target');
    if (!vp || !tgt) return;
    var y = tgt.offsetTop + tgt.offsetHeight / 2 - vp.clientHeight * 0.46;
    root.style.setProperty('--reelY', (-Math.max(0, Math.round(y))) + 'px');
  };
  measureReel();
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(measureReel);
  window.addEventListener('load', measureReel);
  var reelTimer;
  window.addEventListener('resize', function () {
    clearTimeout(reelTimer);
    reelTimer = setTimeout(measureReel, 150);
  });

  /* ---- fallback: drive the flight from scroll (coalesced, passive) ------ */
  if (!hasScrollTL) {
    var queued = false;
    var update = function () {
      queued = false;
      var max = document.documentElement.scrollHeight - window.innerHeight;
      var p = max > 0 ? Math.min(1, Math.max(0, window.scrollY / max)) : 0;
      root.style.setProperty('--sp', p.toFixed(4));
      root.style.setProperty('--depth', Math.round(p * 2400));
      root.classList.toggle('docked', p > 0.08 && p < 0.88);
    };
    var onScroll = function () {
      if (queued) return;
      queued = true;
      requestAnimationFrame(update);
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', onScroll);
    update();
  }

  /* ---- fallback: reveal on enter --------------------------------------- */
  if (!hasViewTL && 'IntersectionObserver' in window) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (!e.isIntersecting) return;
        e.target.classList.add('in');
        io.unobserve(e.target);
      });
    }, { rootMargin: '0px 0px -12% 0px' });
    document.querySelectorAll('.rise').forEach(function (el) { io.observe(el); });
  }
})();
