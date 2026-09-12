"use strict";
var __create = Object.create;
var __defProp = Object.defineProperty;
var __getOwnPropDesc = Object.getOwnPropertyDescriptor;
var __getOwnPropNames = Object.getOwnPropertyNames;
var __getProtoOf = Object.getPrototypeOf;
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
var __toESM = (mod, isNodeMode, target) => (target = mod != null ? __create(__getProtoOf(mod)) : {}, __copyProps(
  // If the importer is in node compatibility mode or this is not an ESM
  // file that has been converted to a CommonJS file using a Babel-
  // compatible transform (i.e. "__esModule" has not been set), then set
  // "default" to the CommonJS "module.exports" for node compatibility.
  isNodeMode || !mod || !mod.__esModule ? __defProp(target, "default", { value: mod, enumerable: true }) : target,
  mod
));
var __toCommonJS = (mod) => __copyProps(__defProp({}, "__esModule", { value: true }), mod);
var websocket_exports = {};
__export(websocket_exports, {
  experimental_upgradeWebSocket: () => experimental_upgradeWebSocket
});
module.exports = __toCommonJS(websocket_exports);
var import_get_context = require("../get-context");
const DEFAULT_MAX_PAYLOAD = 256 * 1024;
async function loadWebSocketServer() {
  try {
    const ws = await import("ws");
    return ws.WebSocketServer;
  } catch {
    throw new Error(
      'The "ws" package is required for experimental_upgradeWebSocket(). Install it with: npm install ws'
    );
  }
}
async function experimental_upgradeWebSocket(handler, options = {}) {
  const ctx = (0, import_get_context.getContext)();
  if (typeof ctx.upgradeWebSocket !== "function") {
    throw new Error(
      "experimental_upgradeWebSocket is not available in the current runtime environment. This feature requires a Vercel runtime that supports WebSocket upgrades."
    );
  }
  const WebSocketServer = await loadWebSocketServer();
  const { req, socket, head } = ctx.upgradeWebSocket();
  const wss = new WebSocketServer({
    noServer: true,
    maxPayload: options.maxPayload ?? DEFAULT_MAX_PAYLOAD
  });
  const ws = await new Promise((resolve, reject) => {
    const cleanup = () => {
      socket.removeListener("error", onError);
      socket.removeListener("close", onClose);
    };
    const rejectUpgrade = (err) => {
      cleanup();
      if (err instanceof Error) {
        reject(err);
        return;
      }
      const error = new Error("WebSocket upgrade failed");
      error.cause = err;
      reject(error);
    };
    const resolveUpgrade = (ws2) => {
      cleanup();
      resolve(ws2);
    };
    const onError = (err) => rejectUpgrade(err);
    const onClose = () => rejectUpgrade(
      new Error("socket closed before the WebSocket upgrade completed")
    );
    socket.once("error", onError);
    socket.once("close", onClose);
    try {
      wss.handleUpgrade(req, socket, head, resolveUpgrade);
    } catch (err) {
      rejectUpgrade(err);
    }
  });
  try {
    await handler(ws);
  } catch (err) {
    ws.close(1011, "WebSocket handler failed");
    throw err;
  }
  return new Response(null, { status: 204 });
}
// Annotate the CommonJS export names for ESM import in node:
0 && (module.exports = {
  experimental_upgradeWebSocket
});
