/*
 * CivicPulse – front-end behaviour.
 * All data comes from the backend API under /api (see backend/server.py).
 */
(function () {
  'use strict';

  var STAGES = ['Reported', 'Acknowledged', 'In Progress', 'Resolved'];
  var STATUS_STAGE = { 'Pending': 0, 'In Progress': 2, 'Resolved': 3 };
  var STATUSES = ['Pending', 'In Progress', 'Resolved'];
  var DEFAULT_IMAGE = 'image/city.jpg';
  var CATEGORY_IMAGE = {
    'Roads': 'image/pothole.jpg',
    'Waste Management': 'image/dustbin.jpg',
    'Streetlights': 'image/streetlight.jpg',
    'Water Supply': 'image/water_leakage.jpg',
    'Drainage': 'image/DRAIN.jpg'
  };
  var CATEGORY_ORDER = ['Roads', 'Waste Management', 'Streetlights', 'Water Supply', 'Drainage', 'Other'];
  var MAX_PHOTO_BYTES = 5 * 1024 * 1024;
  var MIN_PASSWORD = 8;
  var EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

  /* ---------- helpers ---------- */

  function $(id) { return document.getElementById(id); }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function setMessage(node, text, ok) {
    node.textContent = text;
    node.classList.toggle('ok', !!ok);
  }

  function statusClass(status) {
    var map = { 'Pending': 'pending', 'In Progress': 'progress', 'Resolved': 'resolved' };
    return 'status-' + (map[status] || 'pending');
  }

  function statusBadge(status) {
    return el('span', 'status ' + statusClass(status), status);
  }

  function reportCountText(n) {
    return n + (n === 1 ? ' report' : ' reports');
  }

  function formatDate(iso) {
    return new Date(iso.length === 10 ? iso + 'T00:00:00' : iso)
      .toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' });
  }

  function formatDateTime(iso) {
    return new Date(iso).toLocaleString('en-IN', {
      day: 'numeric', month: 'short', year: 'numeric', hour: 'numeric', minute: '2-digit'
    });
  }

  // Calls the backend. Rejects with an Error whose message is safe to show to the user.
  function api(path, options) {
    options = options || {};
    var init = { method: options.method || 'GET', credentials: 'same-origin', headers: {} };
    if (options.body !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(options.body);
    }
    return fetch(path, init).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok) {
          var err = new Error(data.error || 'Something went wrong. Please try again.');
          err.status = res.status;
          throw err;
        }
        return data;
      });
    }, function () {
      throw new Error('Cannot reach the CivicPulse server. Please check your connection and try again.');
    });
  }

  /* ---------- mobile navigation ---------- */

  function initNav() {
    var toggle = $('menuToggle');
    var links = $('navLinks');
    if (!toggle || !links) return;

    function setOpen(open) {
      links.classList.toggle('open', open);
      toggle.setAttribute('aria-expanded', String(open));
      toggle.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
      toggle.innerHTML = open ? '&#10005;' : '&#9776;';
    }

    toggle.addEventListener('click', function () {
      setOpen(!links.classList.contains('open'));
    });
    links.addEventListener('click', function (e) {
      if (e.target.tagName === 'A') setOpen(false);
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') setOpen(false);
    });
    window.addEventListener('resize', function () {
      if (window.innerWidth > 960) setOpen(false);
    });
  }

  /* ---------- home: live stats ---------- */

  function animateCounter(node, target, reduceMotion) {
    if (reduceMotion) { node.textContent = Number(target).toLocaleString('en-IN'); return; }
    var start = null;
    var duration = 1200;
    function step(ts) {
      if (start === null) start = ts;
      var progress = Math.min((ts - start) / duration, 1);
      var eased = 1 - Math.pow(1 - progress, 3);
      node.textContent = Math.round(target * eased).toLocaleString('en-IN');
      if (progress < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }

  function initStats() {
    var nodes = document.querySelectorAll('.stat-number[data-stat]');
    if (!nodes.length) return;

    var reduceMotion = !!(window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);

    api('/api/stats').then(function (stats) {
      nodes.forEach(function (node) {
        node.dataset.target = stats[node.dataset.stat] || 0;
        node.textContent = '0';
      });

      function start(node) {
        animateCounter(node, Number(node.dataset.target), reduceMotion);
      }

      if (!('IntersectionObserver' in window)) {
        nodes.forEach(start);
        return;
      }
      var observer = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            start(entry.target);
            observer.unobserve(entry.target);
          }
        });
      }, { threshold: 0.5 });
      nodes.forEach(function (node) { observer.observe(node); });
    }).catch(function () {
      nodes.forEach(function (node) { node.textContent = '–'; });
    });
  }

  /* ---------- home: recent issues from the database ---------- */

  function initRecent() {
    var grid = $('recentIssues');
    if (!grid) return;

    api('/api/issues?limit=3').then(function (data) {
      grid.textContent = '';
      if (!data.issues.length) {
        grid.appendChild(el('p', 'hint center', 'No reports yet. Be the first to report an issue.'));
        return;
      }
      data.issues.forEach(function (issue) {
        var article = el('article', 'issue-card');
        var img = el('img');
        img.src = issue.photo_url || CATEGORY_IMAGE[issue.category] || DEFAULT_IMAGE;
        img.alt = issue.title;
        img.loading = 'lazy';

        var body = el('div', 'issue-body');
        body.appendChild(el('h3', null, issue.title));
        body.appendChild(el('p', 'issue-meta', issue.locality));
        body.appendChild(el('p', 'issue-meta', issue.category));
        body.appendChild(statusBadge(issue.status));
        var link = el('a', 'btn-outline', 'View Details');
        link.href = 'track.html?id=' + encodeURIComponent(issue.id);
        body.appendChild(link);

        article.appendChild(img);
        article.appendChild(body);
        grid.appendChild(article);
      });
    }).catch(function (err) {
      grid.textContent = '';
      grid.appendChild(el('p', 'hint center', err.message));
    });
  }

  /* ---------- explore: list, search and filters ---------- */

  // One report as a row: ID, status, title, locality, date, link to tracking
  function reportItem(issue) {
    var li = el('li', 'group-item');

    var top = el('div', 'group-item-top');
    top.appendChild(el('strong', null, issue.id));
    top.appendChild(statusBadge(issue.status));
    li.appendChild(top);

    li.appendChild(el('div', 'report-title', issue.title));
    li.appendChild(el('div', 'issue-meta', issue.locality + ' · ' + formatDate(issue.date)));

    var link = el('a', null, 'Track this issue');
    link.href = 'track.html?id=' + encodeURIComponent(issue.id);
    li.appendChild(link);
    return li;
  }

  // One card per category: the newest few reports, plus a link to the full list
  var CARD_PREVIEW = 3;

  function categoryCard(category, items) {
    var article = el('article', 'issue-card group-card');
    article.dataset.category = category;

    var img = el('img');
    img.src = CATEGORY_IMAGE[category] || DEFAULT_IMAGE;
    img.alt = category;
    img.loading = 'lazy';

    var body = el('div', 'issue-body');
    body.appendChild(el('h3', null, category));
    body.appendChild(el('p', 'issue-meta', reportCountText(items.length)));

    var list = el('ul', 'group-list');
    items.slice(0, CARD_PREVIEW).forEach(function (issue) {
      list.appendChild(reportItem(issue));
    });
    body.appendChild(list);

    if (items.length > CARD_PREVIEW) {
      var all = el('a', 'btn-outline view-all', 'View all ' + items.length + ' reports');
      all.href = 'category.html?category=' + encodeURIComponent(category);
      body.appendChild(all);
    }

    article.appendChild(img);
    article.appendChild(body);
    return article;
  }

  function initExplore() {
    var grid = $('issuesGrid');
    if (!grid) return;

    var search = $('issueSearch');
    var category = $('categoryFilter');
    var status = $('statusFilter');
    var count = $('resultCount');
    var noResults = $('noResults');
    var issues = [];

    function apply() {
      var q = search.value.trim().toLowerCase();
      var cat = category.value;
      var st = status.value;
      var groups = {};
      var shown = 0;

      issues.forEach(function (issue) {
        var haystack = (issue.title + ' ' + issue.locality + ' ' + issue.category).toLowerCase();
        var match = (!q || haystack.indexOf(q) !== -1) &&
          (cat === 'all' || issue.category === cat) &&
          (st === 'all' || issue.status === st);
        if (match) {
          (groups[issue.category] = groups[issue.category] || []).push(issue);
          shown++;
        }
      });

      var order = CATEGORY_ORDER.concat(Object.keys(groups).filter(function (c) {
        return CATEGORY_ORDER.indexOf(c) === -1;
      }));

      grid.textContent = '';
      order.forEach(function (c) {
        if (groups[c]) grid.appendChild(categoryCard(c, groups[c]));
      });

      count.textContent = 'Showing ' + shown + ' of ' + issues.length + ' reports';
      noResults.hidden = shown !== 0;
    }

    count.textContent = 'Loading issues…';
    api('/api/issues').then(function (data) {
      issues = data.issues;
      grid.textContent = '';
      apply();
    }).catch(function (err) {
      grid.textContent = '';
      count.textContent = err.message;
    });

    search.addEventListener('input', apply);
    category.addEventListener('change', apply);
    status.addEventListener('change', apply);
  }

  /* ---------- category page: every report in one category ---------- */

  function initCategory() {
    var list = $('categoryList');
    if (!list) return;

    var title = $('categoryTitle');
    var summary = $('categorySummary');
    var notice = $('categoryNotice');
    var image = $('categoryImage');
    var tabs = document.querySelectorAll('.cat-tab');
    var category = new URLSearchParams(window.location.search).get('category') || '';
    var issues = [];
    var activeStatus = 'all';

    function showNotice(text) {
      notice.hidden = !text;
      notice.textContent = text || '';
    }

    function renderCard(issue) {
      var card = el('article', 'cat-card');

      var img = el('img');
      img.src = issue.photo_url || CATEGORY_IMAGE[issue.category] || DEFAULT_IMAGE;
      img.alt = issue.title;
      img.loading = 'lazy';
      card.appendChild(img);

      var body = el('div', 'cat-card-body');
      var top = el('div', 'cat-card-top');
      top.appendChild(el('span', 'cat-card-id', issue.id));
      top.appendChild(statusBadge(issue.status));
      body.appendChild(top);

      body.appendChild(el('h3', null, issue.title));
      body.appendChild(el('p', 'cat-meta', issue.locality));
      body.appendChild(el('p', 'cat-meta', 'Reported ' + formatDate(issue.date)));

      var link = el('a', 'cat-card-link', 'Track this issue →');
      link.href = 'track.html?id=' + encodeURIComponent(issue.id);
      body.appendChild(link);

      card.appendChild(body);
      return card;
    }

    function render() {
      var shown = issues.filter(function (issue) {
        return activeStatus === 'all' || issue.status === activeStatus;
      });

      list.textContent = '';
      shown.forEach(function (issue) { list.appendChild(renderCard(issue)); });

      if (!issues.length) {
        showNotice('No reports in this category yet.');
      } else if (!shown.length) {
        showNotice('No ' + activeStatus.toLowerCase() + ' reports in this category.');
      } else {
        showNotice('');
      }
    }

    function updateCounts() {
      var counts = { all: issues.length, 'Pending': 0, 'In Progress': 0, 'Resolved': 0 };
      issues.forEach(function (issue) { counts[issue.status] = (counts[issue.status] || 0) + 1; });
      document.querySelectorAll('.cat-count').forEach(function (node) {
        node.textContent = counts[node.dataset.count] || 0;
      });
    }

    tabs.forEach(function (tab) {
      tab.addEventListener('click', function () {
        activeStatus = tab.dataset.status;
        tabs.forEach(function (t) {
          var on = t === tab;
          t.classList.toggle('is-active', on);
          t.setAttribute('aria-pressed', String(on));
        });
        render();
      });
    });

    if (!category) {
      title.textContent = 'All categories';
      summary.textContent = '';
      image.src = DEFAULT_IMAGE;
      showNotice('Open a category from the Explore page to see its reports.');
      return;
    }

    title.textContent = category;
    document.title = category + ' – CivicPulse';
    image.src = CATEGORY_IMAGE[category] || DEFAULT_IMAGE;
    image.alt = category;
    summary.textContent = 'Loading reports…';

    api('/api/issues?category=' + encodeURIComponent(category)).then(function (data) {
      issues = data.issues;
      summary.textContent = reportCountText(issues.length) + ' in ' + category;
      updateCounts();
      render();
    }).catch(function (err) {
      summary.textContent = '';
      showNotice(err.message);
    });
  }

  /* ---------- track: look up a complaint ---------- */

  function initTrack() {
    var form = $('trackForm');
    if (!form) return;

    var input = $('complaintId');
    var result = $('trackResult');

    function showNotice(text, className) {
      result.hidden = false;
      result.textContent = '';
      result.appendChild(el('p', className || 'not-found', text));
    }

    function showIssue(data) {
      var issue = data.issue;
      var events = data.events || [];
      var stage = STATUS_STAGE[issue.status] || 0;

      result.hidden = false;
      result.textContent = '';
      result.appendChild(el('p', 'hint', 'Complaint ID: ' + issue.id));
      result.appendChild(el('h2', null, issue.title));
      result.appendChild(el('p', 'issue-meta', issue.locality));
      result.appendChild(el('p', 'issue-meta', issue.category + ' · Reported ' + formatDate(issue.date)));
      result.appendChild(statusBadge(issue.status));

      if (issue.description) {
        result.appendChild(el('p', null, issue.description));
      }

      var timeline = el('ol', 'timeline');
      STAGES.forEach(function (name, i) {
        var cls = i < stage ? 'done' : (i === stage ? 'current' : '');
        timeline.appendChild(el('li', cls, name));
      });
      result.appendChild(timeline);

      if (events.length) {
        result.appendChild(el('h3', null, 'Updates'));
        var list = el('ul', 'updates');
        events.slice().reverse().forEach(function (ev) {
          var text = formatDateTime(ev.date) + ' – ' + ev.status;
          if (ev.note) text += ': ' + ev.note;
          list.appendChild(el('li', null, text));
        });
        result.appendChild(list);
      }
    }

    function lookup(rawId) {
      var clean = String(rawId || '').trim().toUpperCase();
      if (!clean) {
        showNotice('Please enter your complaint ID.');
        return;
      }
      showNotice('Looking up ' + clean + '…', 'hint');
      api('/api/issues/' + encodeURIComponent(clean))
        .then(showIssue)
        .catch(function (err) { showNotice(err.message); });
    }

    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var clean = input.value.trim().toUpperCase();
      input.value = clean;
      try {
        history.replaceState(null, '', clean ? '?id=' + encodeURIComponent(clean) : location.pathname);
      } catch (err) { /* not essential */ }
      lookup(clean);
    });

    // Support links such as track.html?id=CP-1001
    var fromUrl = new URLSearchParams(window.location.search).get('id');
    if (fromUrl) {
      input.value = fromUrl.trim().toUpperCase();
      lookup(fromUrl);
    }
  }

  /* ---------- report an issue ---------- */

  function readFileAsDataUrl(file) {
    return new Promise(function (resolve, reject) {
      var reader = new FileReader();
      reader.onload = function () { resolve(reader.result); };
      reader.onerror = function () { reject(new Error('The photo could not be read.')); };
      reader.readAsDataURL(file);
    });
  }

  function initReport() {
    var form = $('reportForm');
    if (!form) return;

    var msg = $('reportMessage');
    var success = $('reportSuccess');
    var newId = $('newId');
    var trackLink = $('trackNewLink');
    var title = $('title');
    var category = $('category');
    var locality = $('locality');
    var description = $('description');
    var photo = $('photo');
    var submitBtn = form.querySelector('button[type="submit"]');

    form.addEventListener('submit', function (e) {
      e.preventDefault();

      var values = {
        title: title.value.trim(),
        category: category.value,
        locality: locality.value.trim(),
        description: description.value.trim()
      };
      var file = photo.files && photo.files[0];
      var problem = null;

      if (values.title.length < 3) problem = 'Please give the issue a short title (at least 3 characters).';
      else if (!values.category) problem = 'Please choose a category.';
      else if (values.locality.length < 2) problem = 'Please enter the locality.';
      else if (values.description.length < 10) problem = 'Please describe the problem in a few more words.';
      else if (file && !/^image\//.test(file.type)) problem = 'The photo must be an image file.';
      else if (file && file.size > MAX_PHOTO_BYTES) problem = 'The photo must be under 5 MB.';

      if (problem) {
        setMessage(msg, problem, false);
        return;
      }

      submitBtn.disabled = true;
      setMessage(msg, 'Submitting your report…', true);

      var photoReady = file ? readFileAsDataUrl(file) : Promise.resolve(null);
      photoReady
        .then(function (photoData) {
          return api('/api/issues', {
            method: 'POST',
            body: {
              title: values.title,
              category: values.category,
              locality: values.locality,
              description: values.description,
              photo: photoData
            }
          });
        })
        .then(function (data) {
          newId.textContent = data.issue.id;
          trackLink.href = 'track.html?id=' + encodeURIComponent(data.issue.id);
          form.reset();
          msg.textContent = '';
          form.hidden = true;
          success.hidden = false;
          success.scrollIntoView({ behavior: 'smooth', block: 'center' });
        })
        .catch(function (err) {
          setMessage(msg, err.message, false);
        })
        .then(function () {
          submitBtn.disabled = false;
        });
    });
  }

  /* ---------- login and create account ---------- */

  function initLogin() {
    var form = $('loginForm');
    if (!form) return;

    var email = $('email');
    var password = $('password');
    var emailLabel = form.querySelector('label[for="email"]');
    var heading = document.querySelector('.login-card h2');
    var subtitle = document.querySelector('.login-subtitle');
    var submitBtn = form.querySelector('.login-button');
    var toggleLink = document.querySelector('.signup-text a');
    var registering = false;

    var msg = el('p', 'form-message');
    msg.id = 'loginMessage';
    msg.setAttribute('aria-live', 'polite');
    form.parentNode.insertBefore(msg, form.nextSibling);

    // Name field, shown only when creating an account
    var nameLabel = el('label', null, 'Full name');
    nameLabel.htmlFor = 'fullName';
    var name = el('input');
    name.type = 'text';
    name.id = 'fullName';
    name.placeholder = 'Enter your full name';
    name.autocomplete = 'name';
    form.insertBefore(nameLabel, emailLabel);
    form.insertBefore(name, emailLabel);

    function setMode(isRegister) {
      registering = isRegister;
      nameLabel.hidden = name.hidden = !isRegister;
      password.autocomplete = isRegister ? 'new-password' : 'current-password';
      heading.textContent = isRegister ? 'Create your account' : 'Login to CivicPulse';
      subtitle.textContent = isRegister ? 'It only takes a minute.' : 'Enter your details to continue.';
      submitBtn.textContent = isRegister ? 'Create account' : 'Login';
      toggleLink.textContent = isRegister ? 'Already have an account? Login' : 'Create Account';
      msg.textContent = '';
    }

    toggleLink.addEventListener('click', function (e) {
      e.preventDefault();
      setMode(!registering);
    });

    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var address = email.value.trim().toLowerCase();

      if (!EMAIL_RE.test(address)) {
        setMessage(msg, 'Please enter a valid email address.', false);
        return;
      }
      if (registering && name.value.trim().length < 2) {
        setMessage(msg, 'Please enter your full name.', false);
        return;
      }
      if (registering && password.value.length < MIN_PASSWORD) {
        setMessage(msg, 'Password must be at least ' + MIN_PASSWORD + ' characters.', false);
        return;
      }
      if (!password.value) {
        setMessage(msg, 'Please enter your password.', false);
        return;
      }

      submitBtn.disabled = true;
      var request = registering
        ? api('/api/auth/register', { method: 'POST', body: { name: name.value.trim(), email: address, password: password.value } })
        : api('/api/auth/login', { method: 'POST', body: { email: address, password: password.value } });

      request
        .then(function (data) {
          setMessage(msg, 'Welcome, ' + data.user.name + '. Redirecting…', true);
          window.location.href = data.user.role === 'admin' ? 'admin.html' : 'index.html';
        })
        .catch(function (err) {
          setMessage(msg, err.message, false);
          submitBtn.disabled = false;
        });
    });

    // "Forgot Password?" is not built yet
    document.querySelectorAll('.login-options a').forEach(function (link) {
      link.addEventListener('click', function (e) {
        e.preventDefault();
        setMessage(msg, 'Password reset is not available yet. Please contact the administrator.', false);
      });
    });
  }

  /* ---------- admin: manage issue status ---------- */

  function initAdmin() {
    var root = $('adminRoot');
    if (!root) return;

    var notice = $('adminNotice');
    var body = $('adminBody');
    var message = $('adminMessage');
    var logoutBtn = $('logoutBtn');

    function denied(text) {
      notice.textContent = text + ' ';
      var link = el('a', null, 'Go to login');
      link.href = 'login.html';
      notice.appendChild(link);
    }

    function renderRow(issue) {
      var tr = el('tr');

      tr.appendChild(el('td', null, issue.id));

      var nameCell = el('td');
      nameCell.appendChild(el('strong', null, issue.title));
      nameCell.appendChild(el('br'));
      nameCell.appendChild(el('span', 'issue-meta', issue.locality));
      tr.appendChild(nameCell);

      var statusCell = el('td');
      var select = el('select');
      select.setAttribute('aria-label', 'Status for ' + issue.id);
      STATUSES.forEach(function (s) {
        var option = el('option', null, s);
        option.value = s;
        if (s === issue.status) option.selected = true;
        select.appendChild(option);
      });
      statusCell.appendChild(select);
      tr.appendChild(statusCell);

      var noteCell = el('td');
      var note = el('input');
      note.type = 'text';
      note.maxLength = 300;
      note.placeholder = 'e.g. Crew scheduled for Tuesday';
      note.setAttribute('aria-label', 'Note for ' + issue.id);
      noteCell.appendChild(note);
      tr.appendChild(noteCell);

      var saveCell = el('td');
      var save = el('button', 'btn-primary', 'Save');
      save.type = 'button';
      save.addEventListener('click', function () {
        save.disabled = true;
        api('/api/admin/issues/' + encodeURIComponent(issue.id), {
          method: 'PATCH',
          body: { status: select.value, note: note.value.trim() }
        }).then(function () {
          setMessage(message, issue.id + ' updated.', true);
          loadIssues();
        }).catch(function (err) {
          setMessage(message, err.message, false);
          save.disabled = false;
        });
      });
      saveCell.appendChild(save);
      tr.appendChild(saveCell);

      return tr;
    }

    function loadIssues() {
      api('/api/issues').then(function (data) {
        body.textContent = '';
        data.issues.forEach(function (issue) { body.appendChild(renderRow(issue)); });
      }).catch(function (err) {
        setMessage(message, err.message, false);
      });
    }

    api('/api/auth/me').then(function (data) {
      if (!data.user) {
        denied('Please sign in with an administrator account to manage issues.');
        return;
      }
      if (data.user.role !== 'admin') {
        denied('Your account does not have administrator access.');
        return;
      }
      notice.hidden = true;
      root.hidden = false;
      loadIssues();
    }).catch(function (err) {
      denied(err.message);
    });

    logoutBtn.addEventListener('click', function () {
      api('/api/auth/logout', { method: 'POST' }).then(function () {
        window.location.href = 'login.html';
      });
    });
  }

  /* ---------- start ---------- */

  document.addEventListener('DOMContentLoaded', function () {
    initNav();
    initStats();
    initExplore();
    initTrack();
    initReport();
    initLogin();
    initAdmin();
    initCategory();
    initRecent();
  });
})();