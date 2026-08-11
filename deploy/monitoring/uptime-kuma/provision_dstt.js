#!/usr/bin/env node
"use strict";

/*
 * Idempotently provision the DSTT monitors through Uptime Kuma's own socket
 * API. Run this script inside the Uptime Kuma container so it can reuse the
 * application runtime without storing an administrator password.
 */

const crypto = require("crypto");
const fs = require("fs");
const jwt = require("jsonwebtoken");
const sqlite3 = require("@louislam/sqlite3");
const { io } = require("socket.io-client");

const DATA_DIR = process.env.DATA_DIR || "/app/data";
const DB_PATH = `${DATA_DIR}/kuma.db`;
const PUSH_ENV_PATH = `${DATA_DIR}/dstt-monitor-push.env`;
const NOTIFICATION_NAME = "DSTT 障害メール";

function requiredEnvironment(name) {
    const value = (process.env[name] || "").trim();
    if (!value) {
        throw new Error(`${name} is required`);
    }
    return value;
}

function openDatabase() {
    return new Promise((resolve, reject) => {
        const database = new sqlite3.Database(DB_PATH, sqlite3.OPEN_READONLY, (error) => {
            if (error) {
                reject(error);
            } else {
                resolve(database);
            }
        });
    });
}

function get(database, sql, parameters = []) {
    return new Promise((resolve, reject) => {
        database.get(sql, parameters, (error, row) => (error ? reject(error) : resolve(row)));
    });
}

function all(database, sql, parameters = []) {
    return new Promise((resolve, reject) => {
        database.all(sql, parameters, (error, rows) => (error ? reject(error) : resolve(rows)));
    });
}

function closeDatabase(database) {
    return new Promise((resolve, reject) => {
        database.close((error) => (error ? reject(error) : resolve()));
    });
}

function emitWithAck(socket, event, ...arguments_) {
    return new Promise((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error(`${event} timed out`)), 20000);
        socket.emit(event, ...arguments_, (response) => {
            clearTimeout(timer);
            if (!response || response.ok !== true) {
                reject(new Error(`${event} failed: ${response?.msg || "unknown error"}`));
                return;
            }
            resolve(response);
        });
    });
}

function monitorDefaults(overrides) {
    return {
        type: "http",
        name: "",
        active: true,
        parent: null,
        url: "https://",
        method: "GET",
        protocol: null,
        location: "world",
        ipFamily: null,
        interval: 60,
        retryInterval: 60,
        resendInterval: 0,
        maxretries: 0,
        retryOnlyOnStatusCodeFailure: false,
        notificationIDList: {},
        ignoreTls: false,
        upsideDown: false,
        expiryNotification: false,
        domainExpiryNotification: false,
        maxredirects: 10,
        accepted_statuscodes: ["200-299"],
        saveResponse: false,
        saveErrorResponse: true,
        responseMaxLength: 1024,
        dns_resolve_type: "A",
        dns_resolve_server: "",
        proxyId: null,
        authMethod: null,
        httpBodyEncoding: "json",
        kafkaProducerBrokers: [],
        kafkaProducerSaslOptions: { mechanism: "None" },
        kafkaProducerSsl: false,
        kafkaProducerAllowAutoTopicCreation: false,
        rabbitmqNodes: [],
        conditions: [],
        cacheBust: false,
        timeout: 20,
        ping_count: 3,
        ping_numeric: true,
        packetSize: 56,
        ping_per_request_timeout: 2,
        ...overrides,
    };
}

async function main() {
    const smtpUser = requiredEnvironment("DSTT_SMTP_USERNAME");
    const smtpPassword = requiredEnvironment("DSTT_SMTP_PASSWORD");
    const alertTo = requiredEnvironment("DSTT_ALERT_TO");
    const smtpHost = process.env.DSTT_SMTP_HOST || "smtp.gmail.com";
    const smtpPort = Number(process.env.DSTT_SMTP_PORT || "587");

    const database = await openDatabase();
    const user = await get(database, "SELECT id, username, password FROM user WHERE active = 1 ORDER BY id LIMIT 1");
    const secret = await get(database, "SELECT value FROM setting WHERE key = 'jwtSecret'");
    const existingMonitors = await all(database, "SELECT id, name, push_token FROM monitor");
    const existingNotification = await get(
        database,
        "SELECT id, config FROM notification WHERE name = ? ORDER BY id LIMIT 1",
        [NOTIFICATION_NAME]
    );
    await closeDatabase(database);

    if (!user || !secret) {
        throw new Error("Uptime Kuma administrator setup is incomplete");
    }

    const passwordFingerprint = crypto
        .createHash("shake256", { outputLength: 16 })
        .update(user.password)
        .digest("hex");
    const token = jwt.sign({ username: user.username, h: passwordFingerprint }, secret.value);
    const socket = io("http://127.0.0.1:3001", {
        transports: ["websocket"],
        reconnection: false,
        timeout: 15000,
    });

    await new Promise((resolve, reject) => {
        socket.once("connect", resolve);
        socket.once("connect_error", reject);
    });
    await emitWithAck(socket, "loginByToken", token);

    const notification = {
        name: NOTIFICATION_NAME,
        type: "smtp",
        isDefault: true,
        applyExisting: true,
        smtpHost,
        smtpPort,
        smtpSecure: false,
        smtpIgnoreTLSError: false,
        smtpIgnoreSTARTTLS: false,
        smtpUsername: smtpUser,
        smtpPassword,
        smtpFrom: `"DSTT Uptime Kuma" <${smtpUser}>`,
        smtpTo: alertTo,
        smtpCC: "",
        smtpBCC: "",
        customSubject: "",
        customBody: "",
        htmlBody: false,
    };

    let notificationID = existingNotification?.id;
    if (!notificationID) {
        const response = await emitWithAck(socket, "addNotification", notification, null);
        notificationID = response.id;
    }

    const existingByName = new Map(existingMonitors.map((monitor) => [monitor.name, monitor]));
    async function ensureMonitor(specification, attachNotification = true) {
        const existing = existingByName.get(specification.name);
        const notificationIDList = attachNotification ? { [notificationID]: true } : {};
        if (existing) {
            const response = await emitWithAck(socket, "getMonitor", existing.id);
            const updatedMonitor = {
                ...response.monitor,
                ...specification,
                notificationIDList,
            };
            await emitWithAck(socket, "editMonitor", updatedMonitor);
            return existing.id;
        }
        const monitor = monitorDefaults({
            ...specification,
            notificationIDList,
        });
        const response = await emitWithAck(socket, "add", monitor);
        existingByName.set(specification.name, {
            id: response.monitorID,
            name: specification.name,
            push_token: specification.pushToken || null,
        });
        return response.monitorID;
    }

    const groupID = await ensureMonitor(
        {
            type: "group",
            name: "DSTT 本番",
            active: true,
            url: null,
            timeout: 48,
            description: "dipaletteのDSTT本番環境を、別拠点のAsrayHomeから監視します。",
        },
        false
    );

    const common = { parent: groupID };
    await ensureMonitor({
        ...common,
        name: "DSTT 公開レディネス",
        type: "http",
        url: "https://dstt.dipalette.com/health/ready",
        interval: 120,
        retryInterval: 60,
        maxretries: 3,
        expiryNotification: true,
        domainExpiryNotification: true,
        description: "DNS、Cloudflare、TLS、Nginx、Gunicorn、Flask、DBのSELECT 1まで確認する最重要監視。",
    });
    await ensureMonitor({
        ...common,
        name: "DSTT 公開ログイン画面",
        type: "http",
        url: "https://dstt.dipalette.com/auth/login",
        interval: 300,
        retryInterval: 60,
        maxretries: 2,
        description: "利用者が到達するログイン画面を確認。",
    });
    await ensureMonitor({
        ...common,
        name: "DSTT 公開DNS",
        type: "dns",
        url: null,
        hostname: "dstt.dipalette.com",
        dns_resolve_type: "A",
        dns_resolve_server: "1.1.1.1",
        port: 53,
        interval: 300,
        retryInterval: 60,
        maxretries: 2,
        description: "公開DNSの名前解決を独立して確認。",
    });
    await ensureMonitor({
        ...common,
        name: "dipalette Tailscale Ping",
        type: "ping",
        url: null,
        hostname: "100.116.90.83",
        interval: 300,
        retryInterval: 60,
        maxretries: 2,
        timeout: 10,
        description: "公開経路に依存せず、dipalette本体とTailnetの到達性を確認。",
    });
    await ensureMonitor({
        ...common,
        name: "dipalette Tailscale SSH",
        type: "port",
        url: null,
        hostname: "100.116.90.83",
        port: 22,
        interval: 300,
        retryInterval: 60,
        maxretries: 2,
        description: "障害対応に必要なSSH管理経路を確認。",
    });

    for (const pushSpecification of [
        {
            name: "dipalette 内部健全性",
            interval: 420,
            description: "dstt/nginx、ローカルreadiness、失敗unit、ディスク、メモリを5分ごとに確認。",
        },
        {
            name: "DSTT オフサイトバックアップ",
            interval: 93600,
            description: "dipaletteからAsrayHomeへの検証済みバックアップが26時間以内に完了したことを確認。",
        },
    ]) {
        const existing = existingByName.get(pushSpecification.name);
        await ensureMonitor({
            ...common,
            ...pushSpecification,
            type: "push",
            url: null,
            retryInterval: pushSpecification.interval,
            pushToken: existing?.push_token || crypto.randomBytes(16).toString("hex"),
            timeout: 48,
        });
    }

    const verificationDatabase = await openDatabase();
    const pushRows = await all(
        verificationDatabase,
        "SELECT name, push_token FROM monitor WHERE name IN (?, ?) ORDER BY name",
        ["dipalette 内部健全性", "DSTT オフサイトバックアップ"]
    );
    const monitorCount = await get(verificationDatabase, "SELECT COUNT(*) AS count FROM monitor");
    await closeDatabase(verificationDatabase);

    const pushByName = new Map(pushRows.map((row) => [row.name, row.push_token]));
    const pushEnvironment = [
        `DSTT_INTERNAL_PUSH_URL=http://127.0.0.1:3001/api/push/${pushByName.get("dipalette 内部健全性")}`,
        `DSTT_BACKUP_PUSH_URL=http://127.0.0.1:3001/api/push/${pushByName.get("DSTT オフサイトバックアップ")}`,
        "",
    ].join("\n");
    fs.writeFileSync(PUSH_ENV_PATH, pushEnvironment, { mode: 0o600 });
    fs.chmodSync(PUSH_ENV_PATH, 0o600);

    if (process.argv.includes("--test-notification")) {
        await emitWithAck(socket, "testNotification", notification);
    }
    socket.close();

    console.log(JSON.stringify({
        ok: true,
        monitors: monitorCount.count,
        notification: NOTIFICATION_NAME,
        pushEnvironment: PUSH_ENV_PATH,
        notificationTested: process.argv.includes("--test-notification"),
    }));
}

main().catch((error) => {
    console.error(error.message);
    process.exitCode = 1;
});
