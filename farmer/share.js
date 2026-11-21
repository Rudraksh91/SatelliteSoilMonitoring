/* AgriSat share helpers: turn today's advice into a short WhatsApp message (and the wa.me link that opens it).
 * UMD, no network and no DOM: the page decides when to open the link, so nothing is sent until the farmer taps Send inside WhatsApp.
 * Works in the browser (window.AgriShare) and in Node (tests/test_share.js). */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.AgriShare = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';
  const MAX_LEN = 1200;                       // a message this long is already too long to read on a phone; wa.me links also have a length limit

  /** Strip the small bits of HTML the app's wording carries (<b>…</b>, &nbsp;) so the text is plain. */
  function plain(s) {
    return String(s == null ? '' : s).replace(/<[^>]*>/g, '').replace(/&nbsp;/g, ' ').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
      .replace(/\s+/g, ' ').trim();
  }
  /** WhatsApp turns *x*, _x_ and ~x~ into formatting: a field called "My_farm_1" must not become italic. */
  const clean = s => plain(s).replace(/[*_~`]/g, ' ').replace(/\s+/g, ' ').trim();

  /** parts: { title, head, lines: [..], note, url }. Layout: bold title, bold headline, a blank line, the detail lines, italic note, link. */
  function whatsappText(parts) {
    const p = parts || {};
    const lines = (p.lines || []).map(clean).filter(Boolean);
    const out = [];
    if (clean(p.title)) out.push('*' + clean(p.title) + '*');
    if (clean(p.head)) out.push('*' + clean(p.head) + '*');
    if (lines.length) { out.push(''); lines.forEach(l => out.push(l)); }
    if (clean(p.note)) { out.push(''); out.push('_' + clean(p.note) + '_'); }
    if (p.url) out.push(String(p.url).trim());
    let text = out.join('\n');
    if (text.length > MAX_LEN) text = text.slice(0, MAX_LEN - 1).replace(/\s+\S*$/, '') + '…';
    return text;
  }

  /** wa.me link. With a phone number (digits, with country code) it opens that chat; without one WhatsApp lets the farmer pick who gets it. */
  function whatsappUrl(text, phone) {
    const digits = String(phone == null ? '' : phone).replace(/\D/g, '');
    const to = digits.length >= 8 && digits.length <= 15 ? digits : '';
    return 'https://wa.me/' + to + '?text=' + encodeURIComponent(String(text || ''));
  }
  return { plain, whatsappText, whatsappUrl, MAX_LEN };
});
