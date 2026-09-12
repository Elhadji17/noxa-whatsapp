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
var build_client_exports = {};
__export(build_client_exports, {
  BuildCache: () => BuildCache
});
module.exports = __toCommonJS(build_client_exports);
var import_index = require("./index");
class BuildCache {
  constructor({
    endpoint,
    headers,
    onError,
    timeout = 500
  }) {
    this.get = async (key) => {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), this.timeout);
      try {
        const res = await fetch(`${this.endpoint}${key}`, {
          headers: this.headers,
          method: "GET",
          signal: controller.signal
        });
        if (res.status === 404) {
          clearTimeout(timeoutId);
          return null;
        }
        if (res.status === 200) {
          const cacheState = res.headers.get(
            import_index.HEADERS_VERCEL_CACHE_STATE
          );
          if (cacheState !== import_index.PkgCacheState.Fresh) {
            res.body?.cancel?.();
            clearTimeout(timeoutId);
            return null;
          }
          const result = await res.json();
          clearTimeout(timeoutId);
          return result;
        } else {
          clearTimeout(timeoutId);
          throw new Error(`Failed to get cache: ${res.statusText}`);
        }
      } catch (error) {
        clearTimeout(timeoutId);
        if (error.name === "AbortError") {
          const timeoutError = new Error(
            `Cache request timed out after ${this.timeout}ms`
          );
          timeoutError.stack = error.stack;
          this.onError?.(timeoutError);
        } else {
          this.onError?.(error);
        }
        return null;
      }
    };
    this.set = async (key, value, options) => {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), this.timeout);
      try {
        const optionalHeaders = {};
        if (options?.ttl) {
          optionalHeaders[import_index.HEADERS_VERCEL_REVALIDATE] = options.ttl.toString();
        }
        if (options?.tags && options.tags.length > 0) {
          optionalHeaders[import_index.HEADERS_VERCEL_CACHE_TAGS] = options.tags.join(",");
        }
        if (options?.name) {
          optionalHeaders[import_index.HEADERS_VERCEL_CACHE_ITEM_NAME] = options.name;
        }
        const res = await fetch(`${this.endpoint}${key}`, {
          method: "POST",
          headers: {
            ...this.headers,
            ...optionalHeaders
          },
          body: JSON.stringify(value),
          signal: controller.signal
        });
        clearTimeout(timeoutId);
        if (res.status !== 200) {
          throw new Error(`Failed to set cache: ${res.status} ${res.statusText}`);
        }
      } catch (error) {
        clearTimeout(timeoutId);
        if (error.name === "AbortError") {
          const timeoutError = new Error(
            `Cache request timed out after ${this.timeout}ms`
          );
          timeoutError.stack = error.stack;
          this.onError?.(timeoutError);
        } else {
          this.onError?.(error);
        }
      }
    };
    this.delete = async (key) => {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), this.timeout);
      try {
        const res = await fetch(`${this.endpoint}${key}`, {
          method: "DELETE",
          headers: this.headers,
          signal: controller.signal
        });
        clearTimeout(timeoutId);
        if (res.status !== 200) {
          throw new Error(`Failed to delete cache: ${res.statusText}`);
        }
      } catch (error) {
        clearTimeout(timeoutId);
        if (error.name === "AbortError") {
          const timeoutError = new Error(
            `Cache request timed out after ${this.timeout}ms`
          );
          timeoutError.stack = error.stack;
          this.onError?.(timeoutError);
        } else {
          this.onError?.(error);
        }
      }
    };
    this.expireTag = async (tag) => {
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), this.timeout);
      try {
        if (Array.isArray(tag)) {
          tag = tag.join(",");
        }
        const res = await fetch(`${this.endpoint}revalidate?tags=${tag}`, {
          method: "POST",
          headers: this.headers,
          signal: controller.signal
        });
        clearTimeout(timeoutId);
        if (res.status !== 200) {
          throw new Error(`Failed to revalidate tag: ${res.statusText}`);
        }
      } catch (error) {
        clearTimeout(timeoutId);
        if (error.name === "AbortError") {
          const timeoutError = new Error(
            `Cache request timed out after ${this.timeout}ms`
          );
          timeoutError.stack = error.stack;
          this.onError?.(timeoutError);
        } else {
          this.onError?.(error);
        }
      }
    };
    this.endpoint = endpoint;
    this.headers = headers;
    this.onError = onError;
    this.timeout = timeout;
  }
}
// Annotate the CommonJS export names for ESM import in node:
0 && (module.exports = {
  BuildCache
});
