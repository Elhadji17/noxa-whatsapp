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
var purge_exports = {};
__export(purge_exports, {
  dangerouslyDeleteBySrcImage: () => dangerouslyDeleteBySrcImage,
  dangerouslyDeleteByTag: () => dangerouslyDeleteByTag,
  invalidateBySrcImage: () => invalidateBySrcImage,
  invalidateByTag: () => invalidateByTag
});
module.exports = __toCommonJS(purge_exports);
var import_get_context = require("../get-context");
const invalidateByTag = (tag) => {
  const api = (0, import_get_context.getContext)().purge;
  if (api) {
    return api.invalidateByTag(tag);
  }
  return Promise.resolve();
};
const dangerouslyDeleteByTag = (tag, options) => {
  const api = (0, import_get_context.getContext)().purge;
  if (api) {
    return api.dangerouslyDeleteByTag(tag, options);
  }
  return Promise.resolve();
};
const invalidateBySrcImage = (src) => {
  const api = (0, import_get_context.getContext)().purge;
  return api ? api.invalidateBySrcImage(src) : Promise.resolve();
};
const dangerouslyDeleteBySrcImage = (src, options) => {
  const api = (0, import_get_context.getContext)().purge;
  return api ? api.dangerouslyDeleteBySrcImage(src, options) : Promise.resolve();
};
// Annotate the CommonJS export names for ESM import in node:
0 && (module.exports = {
  dangerouslyDeleteBySrcImage,
  dangerouslyDeleteByTag,
  invalidateBySrcImage,
  invalidateByTag
});
