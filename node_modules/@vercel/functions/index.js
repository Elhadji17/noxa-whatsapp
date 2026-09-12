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
var src_exports = {};
__export(src_exports, {
  addCacheTag: () => import_addcachetag.addCacheTag,
  attachDatabasePool: () => import_db_connections.attachDatabasePool,
  dangerouslyDeleteBySrcImage: () => import_purge.dangerouslyDeleteBySrcImage,
  dangerouslyDeleteByTag: () => import_purge.dangerouslyDeleteByTag,
  experimental_attachDatabasePool: () => import_db_connections.experimental_attachDatabasePool,
  experimental_upgradeWebSocket: () => import_websocket.experimental_upgradeWebSocket,
  geolocation: () => import_headers.geolocation,
  getCache: () => import_cache.getCache,
  getDeadline: () => import_deadline.getDeadline,
  getEnv: () => import_get_env.getEnv,
  invalidateBySrcImage: () => import_purge.invalidateBySrcImage,
  invalidateByTag: () => import_purge.invalidateByTag,
  ipAddress: () => import_headers.ipAddress,
  metric: () => import_metric.metric,
  next: () => import_middleware.next,
  rewrite: () => import_middleware.rewrite,
  waitUntil: () => import_wait_until.waitUntil
});
module.exports = __toCommonJS(src_exports);
var import_headers = require("./headers");
var import_get_env = require("./get-env");
var import_deadline = require("./deadline");
var import_wait_until = require("./wait-until");
var import_metric = require("./metric");
var import_middleware = require("./middleware");
var import_cache = require("./cache");
var import_db_connections = require("./db-connections");
var import_purge = require("./purge");
var import_addcachetag = require("./addcachetag");
var import_websocket = require("./websocket");
// Annotate the CommonJS export names for ESM import in node:
0 && (module.exports = {
  addCacheTag,
  attachDatabasePool,
  dangerouslyDeleteBySrcImage,
  dangerouslyDeleteByTag,
  experimental_attachDatabasePool,
  experimental_upgradeWebSocket,
  geolocation,
  getCache,
  getDeadline,
  getEnv,
  invalidateBySrcImage,
  invalidateByTag,
  ipAddress,
  metric,
  next,
  rewrite,
  waitUntil
});
