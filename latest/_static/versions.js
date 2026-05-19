// Hydrates the #gdxpds-version-selector dropdown from versions.json at the docs root.
// Layout on gh-pages:
//   /latest/<page>
//   /v1.5.0/<page>
//   /versions.json   (siblings: ["latest", "v1.5.0", ...])
// On local builds versions.json is absent and the widget hides itself.
(function () {
  const widget = document.getElementById('gdxpds-version-selector');
  if (!widget) return;
  // The div starts with `hidden` so there's no flash of an empty blue strip
  // before this script runs. We only un-hide it on the successful populate path.

  const path = window.location.pathname;
  const match = path.match(/^(.*?)\/(latest|v[^/]+)(\/.*)?$/);
  if (!match) return;  // Local build or non-versioned URL: stay hidden.
  const docsRoot = match[1];
  const currentVersion = match[2];
  const pageTail = match[3] || '/';
  const versionsUrl = docsRoot + '/versions.json';

  fetch(versionsUrl, { cache: 'no-store' })
    .then(function (r) {
      if (!r.ok) throw new Error('versions.json missing');
      return r.json();
    })
    .then(function (versions) {
      if (!Array.isArray(versions) || versions.length === 0) return;
      const select = document.createElement('select');
      select.id = 'gdxpds-version-select';
      versions.forEach(function (v) {
        const opt = document.createElement('option');
        opt.value = v;
        opt.textContent = v;
        if (v === currentVersion) opt.selected = true;
        select.appendChild(opt);
      });
      select.addEventListener('change', function () {
        window.location.href = docsRoot + '/' + select.value + pageTail;
      });
      const label = document.createElement('span');
      label.textContent = 'Version: ';
      widget.appendChild(label);
      widget.appendChild(select);
      widget.hidden = false;
    })
    .catch(function () { /* stay hidden */ });
})();
