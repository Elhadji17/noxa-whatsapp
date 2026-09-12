"use strict";
var __defProp = Object.defineProperty;
var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
var __getOwnPropNames = Object.getOwnPropertyNames;
var __hasOwnProp = Object.prototype.hasOwnProperty;
var __export = (target, all) => {
  for (var name in all)
    __defProp(target, name, { get: all[name], enumerable: true });
};
var __copyProps = (to, from, except, desc) => {
  if (from && typeof from === "object" || typeof from === "function") {
    for (let key of __getOwnPropNames(from))
      if (!__hasOwnProp.call(to, key) && key !== except)
        __defProp(to, key, { get: () => from[key], enumerable: !(desc = __getOwnPropDesc(from, key)) || desc.enumerable });
  }
  return to;
};
var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: true }), mod);
var addcachetag_exports = {};
__export(addcachetag_exports, {
  addCacheTag: () => addCacheTag
});
module.exports = __toCommonJS(addcachetag_exports);
var import_get_context = require("../get-context");
const addCacheTag = (tag) => {
  const addCacheTag2 = (0, import_get_context.getContext)().addCacheTag;
  if (addCacheTag2) {
    return addCacheTag2(tag);
  }
  return Promise.resolve();
};
// Annotate the CommonJS export names for ESM import in node:
0 && (module.exports = {
  addCacheTag
});
