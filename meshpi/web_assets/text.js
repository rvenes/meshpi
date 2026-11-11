"use strict";

// Literal UI text only; arbitrary message contents and node names are never translated.
function webText(text, ...values) {
  const key = Array.isArray(text) ? text.reduce((out, part, i) =>
    out + part + (i < values.length ? `{${i}}` : ""), "") : text;
  if (typeof meshpiI18n !== "undefined") return meshpiI18n.t(key, values);
  return String(key).replace(/\{(\d+)\}/g, (match, index) =>
    Number(index) < values.length ? String(values[Number(index)]) : match);
}
