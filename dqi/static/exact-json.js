/* Keep source numbers exact for display while ordinary integer counts stay numeric. */
(function () {
  "use strict";

  function parseJSON(source) {
    if (typeof source !== "string") throw new TypeError("JSON input must be a string.");
    let offset = 0;

    function fail() { throw new SyntaxError(`Invalid JSON at position ${offset}.`); }
    function whitespace() {
      while (offset < source.length && " \t\r\n".includes(source[offset])) offset += 1;
    }
    function digit(character) { return character >= "0" && character <= "9"; }
    function string() {
      if (source[offset] !== '"') fail();
      const start = offset++;
      while (offset < source.length) {
        const character = source[offset++];
        if (character === '"') return JSON.parse(source.slice(start, offset));
        if (character === "\\") offset += 1;
      }
      fail();
    }
    function number() {
      const start = offset;
      if (source[offset] === "-") offset += 1;
      if (source[offset] === "0") offset += 1;
      else {
        if (!digit(source[offset]) || source[offset] === "0") fail();
        while (digit(source[offset])) offset += 1;
      }
      let exactText = false;
      if (source[offset] === ".") {
        exactText = true;
        offset += 1;
        if (!digit(source[offset])) fail();
        while (digit(source[offset])) offset += 1;
      }
      if (source[offset] === "e" || source[offset] === "E") {
        exactText = true;
        offset += 1;
        if (source[offset] === "+" || source[offset] === "-") offset += 1;
        if (!digit(source[offset])) fail();
        while (digit(source[offset])) offset += 1;
      }
      const token = source.slice(start, offset);
      if (exactText) return token;
      const numeric = Number(token);
      return Number.isSafeInteger(numeric) ? numeric : token;
    }
    function value() {
      whitespace();
      const character = source[offset];
      if (character === '"') return string();
      if (character === "-" || digit(character)) return number();
      if (character === "[") {
        offset += 1;
        whitespace();
        const result = [];
        if (source[offset] === "]") { offset += 1; return result; }
        while (true) {
          result.push(value());
          whitespace();
          if (source[offset] === "]") { offset += 1; return result; }
          if (source[offset++] !== ",") fail();
        }
      }
      if (character === "{") {
        offset += 1;
        whitespace();
        const result = {};
        if (source[offset] === "}") { offset += 1; return result; }
        while (true) {
          whitespace();
          const key = string();
          whitespace();
          if (source[offset++] !== ":") fail();
          const entry = value();
          // JSON keys are data, including __proto__; duplicate keys use the last value.
          Object.defineProperty(result, key, {value: entry, enumerable: true, configurable: true, writable: true});
          whitespace();
          if (source[offset] === "}") { offset += 1; return result; }
          if (source[offset++] !== ",") fail();
        }
      }
      for (const [token, result] of [["true", true], ["false", false], ["null", null]]) {
        if (source.startsWith(token, offset)) { offset += token.length; return result; }
      }
      fail();
    }

    const result = value();
    whitespace();
    if (offset !== source.length) fail();
    return result;
  }

  globalThis.DQIParseJSON = parseJSON;
})();
