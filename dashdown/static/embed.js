// Dashdown embeddable-page loader.
//
// Drop this on any external page to embed a Dashdown dashboard page as an
// auto-resizing iframe — no Dashdown code or build step needed on the host:
//
//   <script src="https://your-dashboard.example/_dashdown/static/embed.js"
//           data-dashdown-page="/sales"
//           data-dashdown-token="..."         // required when the dashboard has auth
//           data-dashdown-theme="light"></script>
//
// The script self-locates, derives the dashboard origin from its own `src`, and
// inserts an <iframe> pointed at <origin><page>?_embed=<token|1>. The embedded
// page posts its height back (embed_frame.js); we resize the iframe to fit.
//
// Standalone, NOT an ES module — it runs verbatim on the host page, so no
// imports and no modern-only syntax assumptions beyond URL/postMessage.

(function () {
  "use strict";

  var script =
    document.currentScript ||
    (function () {
      var all = document.getElementsByTagName("script");
      return all[all.length - 1];
    })();
  if (!script) return;

  var page = script.getAttribute("data-dashdown-page");
  if (!page) {
    console.error("dashdown embed: missing data-dashdown-page attribute");
    return;
  }
  var token = script.getAttribute("data-dashdown-token");
  var theme = script.getAttribute("data-dashdown-theme");
  var initialHeight = script.getAttribute("data-dashdown-height") || "480";

  // The dashboard origin is wherever this script was served from.
  var origin;
  try {
    origin = new URL(script.src).origin;
  } catch (e) {
    console.error("dashdown embed: could not resolve script origin", e);
    return;
  }

  var path = page.charAt(0) === "/" ? page : "/" + page;
  var qs = "_embed=" + encodeURIComponent(token || "1");
  if (theme) qs += "&_theme=" + encodeURIComponent(theme);
  var src = origin + path + (path.indexOf("?") >= 0 ? "&" : "?") + qs;

  var iframe = document.createElement("iframe");
  iframe.src = src;
  iframe.style.width = "100%";
  iframe.style.border = "0";
  iframe.style.height = initialHeight + "px";
  iframe.setAttribute("loading", "lazy");
  iframe.setAttribute("title", "Dashdown dashboard");
  iframe.setAttribute("scrolling", "no");
  iframe.setAttribute("allow", "clipboard-write");

  script.parentNode.insertBefore(iframe, script.nextSibling);

  // Resize to fit when *this* iframe reports its content height.
  window.addEventListener("message", function (event) {
    if (event.origin !== origin) return; // only our dashboard origin
    if (event.source !== iframe.contentWindow) return; // only this iframe
    var data = event.data;
    if (!data || data.type !== "dashdown:resize") return;
    var h = parseInt(data.height, 10);
    if (h > 0) iframe.style.height = h + "px";
  });
})();
