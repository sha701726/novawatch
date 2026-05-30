"""
Nova – Flask backend

"""

# ── Standard library ──────────────────────────────────────────────────────────
import base64
import hashlib
import hmac
import io
import json
import logging
import math
import os
import random
import re as _re
import smtplib
import struct
import threading
import time
import uuid
from datetime import datetime, time as dtime
from email.message import EmailMessage
from email.mime.application import MIMEApplication as _MIMEApp
from email.mime.multipart import MIMEMultipart as _MIMEMulti
from email.mime.text import MIMEText as _MIMEText
from typing import Any, Optional
import tempfile

# ── Third-party ───────────────────────────────────────────────────────────────
import mysql.connector
from cryptography.fernet import Fernet
from flask import Flask, Response, jsonify, request, send_file, send_from_directory, session

# ── Load .env for local development ──────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # On Render, env vars are set natively; dotenv is optional locally


# =============================================================================
# CONFIGURATION
# =============================================================================

class Config:
    """Central configuration loaded from environment variables."""

    # Flask
    SESSION_SECRET: str = os.getenv("FLASK_SESSION_SECRET", "").strip()
    DEBUG: bool         = os.getenv("FLASK_DEBUG", "0") == "1"
    PORT: int           = int(os.getenv("PORT", "5000"))
    # App base URL for deep links (set in .env for production, e.g. https://nova.example.com)
    APP_BASE_URL: str   = os.getenv("APP_BASE_URL", "").strip()

    # Database
    DB_HOST:     str = os.getenv("DB_HOST",     "localhost")
    DB_PORT:     int = int(os.getenv("DB_PORT",  "3306") or "3306")
    DB_USER:     str = os.getenv("DB_USER",     "root")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "")
    DB_NAME:     str = os.getenv("DB_NAME",     "nova")
    DB_SSL_MODE: str = (os.getenv("DB_SSL_MODE", "") or "").strip().upper()
    DB_SSL_CA_PEM: str = (
        os.getenv("DB_SSL_CA_PEM", "")
        .replace("\\n", "\n")
        .strip('"')
        .strip()
    )

    # QR / Fernet
    FERNET_KEY: str = os.getenv("QR_FERNET_KEY", "").strip()

    # Google OAuth
    GOOGLE_CLIENT_ID:     str = os.getenv("GOOGLE_CLIENT_ID",     "").strip()
    GOOGLE_CLIENT_SECRET: str = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()

    # Razorpay
    RAZORPAY_KEY_ID:     str = os.getenv("RAZORPAY_KEY_ID",     "").strip()
    RAZORPAY_KEY_SECRET: str = os.getenv("RAZORPAY_KEY_SECRET", "").strip()

    # SMTP
    SMTP_HOST:     str = os.getenv("SMTP_HOST",     "smtp.gmail.com")
    SMTP_PORT:     int = int(os.getenv("SMTP_PORT",  "587") or "587")
    SMTP_USER:     str = os.getenv("SMTP_USER",     "").strip().strip('"')
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "").strip().strip('"')

    # Store admin
    STORE_ADMIN_CODE_NAME: str = os.getenv("STORE_ADMIN_CODE_NAME", "SHADOW").strip().upper()

    # Branding (used in all emails & PDFs)
    COMPANY_NAME:   str = os.getenv("COMPANY_NAME",   "Nova Watch").strip()
    WEBSITE_URL:    str = os.getenv("WEBSITE_URL",    "https://novawatch.onrender.com").strip()
    SUPPORT_EMAIL:  str = os.getenv("SUPPORT_EMAIL",  os.getenv("SMTP_USER", "sha701726@gmail.com")).strip()
    LOGO_URL:       str = os.getenv("LOGO_URL",       "https://avatars.githubusercontent.com/u/79453963?v=4").strip()

    @classmethod
    def validate(cls) -> None:
        """Raise RuntimeError for missing mandatory secrets."""
        if not cls.FERNET_KEY:
            raise RuntimeError(
                "QR_FERNET_KEY is not set. "
                "Generate: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )
        if not cls.SESSION_SECRET:
            raise RuntimeError(
                "FLASK_SESSION_SECRET is not set. "
                "Generate: python -c \"import secrets; print(secrets.token_hex(32))\""
            )


Config.validate()


# =============================================================================
# ANTI-CHEAT CONSTANTS
# =============================================================================

MIN_RUNNING_SPEED_KMH    = 3.0
MAX_RUNNING_SPEED_KMH    = 20.0
COINS_PER_10KM           = 1
MAX_COINS_PER_DAY        = 1
MAX_GPS_ACCURACY_METERS  = 50
MIN_SESSION_KM           = 10.0
RUN_WINDOW_START_HOUR    = 5
RUN_WINDOW_END_HOUR      = 8
IST_OFFSET_SEC           = 19800   # UTC+5:30
SPEED_VARIANCE_MIN_KMPH  = 1.5
OTP_EXPIRY_SEC           = 300


# =============================================================================
# DATABASE MANAGER  (with connection pool)
# =============================================================================

class DatabaseManager:
    """Handles all DB connection via a persistent pool + thread-safe schema init."""

    _init_lock: threading.Lock = threading.Lock()
    _init_done: bool           = False

    # ── Connection pool (lazily created once) ─────────────────────────────────
    _pool_lock: threading.Lock           = threading.Lock()
    _pool: Optional[Any]                 = None   # mysql.connector.pooling.MySQLConnectionPool
    _POOL_SIZE: int                      = 10
    _POOL_NAME: str                      = "nova_pool"

    # ── SSL CA path written once ──────────────────────────────────────────────
    _ssl_ca_path: Optional[str]          = None
    _ssl_ca_lock: threading.Lock         = threading.Lock()

    @classmethod
    def _ssl_kwargs(cls) -> dict:
        """Build SSL kwargs — CA PEM written to disk only once."""
        if Config.DB_SSL_MODE not in {"REQUIRED", "VERIFY_CA", "VERIFY_IDENTITY"}:
            return {}
        if Config.DB_SSL_CA_PEM:
            if cls._ssl_ca_path is None:
                with cls._ssl_ca_lock:
                    if cls._ssl_ca_path is None:
                        ca_path = os.path.join(tempfile.gettempdir(), "db-ca.pem")
                        with open(ca_path, "w", encoding="utf-8") as f:
                            f.write(Config.DB_SSL_CA_PEM)
                        cls._ssl_ca_path = ca_path
            return {
                "ssl_ca": cls._ssl_ca_path,
                "ssl_verify_cert": Config.DB_SSL_MODE in {"VERIFY_CA", "VERIFY_IDENTITY"},
            }
        return {"ssl_disabled": False}

    @classmethod
    def _ensure_database_exists(cls) -> None:
        """
        Connect WITHOUT specifying a database and CREATE it if missing.
        Safe to call on a completely blank server.
        """
        ssl_kw = cls._ssl_kwargs()
        conn = mysql.connector.connect(
            host=Config.DB_HOST,
            port=Config.DB_PORT,
            user=Config.DB_USER,
            password=Config.DB_PASSWORD,
            **ssl_kw,
        )
        cur = conn.cursor()
        try:
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{Config.DB_NAME}` "
                f"CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
            conn.commit()
        finally:
            cur.close()
            conn.close()

    @classmethod
    def _get_pool(cls):
        """Return the shared connection pool, creating it if needed."""
        if cls._pool is not None:
            return cls._pool
        with cls._pool_lock:
            if cls._pool is not None:
                return cls._pool
            from mysql.connector.pooling import MySQLConnectionPool
            ssl_kw = cls._ssl_kwargs()
            cls._pool = MySQLConnectionPool(
                pool_name=cls._POOL_NAME,
                pool_size=cls._POOL_SIZE,
                pool_reset_session=True,
                host=Config.DB_HOST,
                port=Config.DB_PORT,
                user=Config.DB_USER,
                password=Config.DB_PASSWORD,
                database=Config.DB_NAME,
                autocommit=False,
                connection_timeout=10,
                **ssl_kw,
            )
            logging.info("[DB] Connection pool created (size=%d)", cls._POOL_SIZE)
        return cls._pool

    @classmethod
    def get_connection(cls):
        """Get a pooled connection. Falls back to direct connect if pool fails."""
        try:
            return cls._get_pool().get_connection()
        except Exception as exc:
            logging.warning("[DB] Pool get failed (%s), falling back to direct connect", exc)
            return mysql.connector.connect(
                host=Config.DB_HOST,
                port=Config.DB_PORT,
                user=Config.DB_USER,
                password=Config.DB_PASSWORD,
                database=Config.DB_NAME,
                connection_timeout=10,
                **cls._ssl_kwargs(),
            )

    @classmethod
    def ensure_tables_once(cls) -> None:
        """
        Thread-safe one-time schema initialisation.
        Step 1: CREATE DATABASE IF NOT EXISTS  (blank-server safe)
        Step 2: CREATE TABLE IF NOT EXISTS     (for every table)
        Step 3: ADD COLUMN IF NOT EXISTS       (for every column in every table)
        """
        if cls._init_done:
            return
        with cls._init_lock:
            if cls._init_done:
                return
            try:
                cls._ensure_database_exists()
                cls._create_tables()
                cls._init_done = True  # only mark done on success so retries work on DB restart
            except Exception as exc:
                logging.error("[DB] Schema init failed: %s — will retry on next request", exc)

    # ── Schema definition ──────────────────────────────────────────────────────
    # Each entry: (table_name, create_sql, [(col_name, col_definition), ...])
    _SCHEMA: list = [
        (
            "NAMES",
            """CREATE TABLE IF NOT EXISTS NAMES (
                USER_ID VARCHAR(20) PRIMARY KEY,
                CODE_NAME VARCHAR(20) UNIQUE NOT NULL,
                PASS_KEY VARCHAR(255) NOT NULL,
                FULL_NAME VARCHAR(60) DEFAULT '',
                EMAIL VARCHAR(100) DEFAULT '',
                PHONE VARCHAR(15) DEFAULT '',
                GOOGLE_ID VARCHAR(128) DEFAULT NULL,
                EMAIL_VERIFIED TINYINT(1) DEFAULT 0,
                PHONE_VERIFIED TINYINT(1) DEFAULT 0,
                CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB""",
            [
                ("FULL_NAME",      "VARCHAR(60) DEFAULT ''"),
                ("EMAIL",          "VARCHAR(100) DEFAULT ''"),
                ("PHONE",          "VARCHAR(15) DEFAULT ''"),
                ("GOOGLE_ID",      "VARCHAR(128) DEFAULT NULL"),
                ("EMAIL_VERIFIED", "TINYINT(1) DEFAULT 0"),
                ("PHONE_VERIFIED", "TINYINT(1) DEFAULT 0"),
                ("CREATED_AT",     "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
            ],
        ),
        (
            "OTP_STORE",
            """CREATE TABLE IF NOT EXISTS OTP_STORE (
                ID INT AUTO_INCREMENT PRIMARY KEY,
                TARGET VARCHAR(120) NOT NULL,
                OTP_CODE VARCHAR(6) NOT NULL,
                PURPOSE VARCHAR(20) NOT NULL,
                EXPIRES_AT BIGINT NOT NULL,
                USED TINYINT(1) DEFAULT 0,
                CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                INDEX idx_target_purpose (TARGET, PURPOSE)
            ) ENGINE=InnoDB""",
            [
                ("TARGET",     "VARCHAR(120) NOT NULL DEFAULT ''"),
                ("OTP_CODE",   "VARCHAR(6) NOT NULL DEFAULT ''"),
                ("PURPOSE",    "VARCHAR(20) NOT NULL DEFAULT ''"),
                ("EXPIRES_AT", "BIGINT NOT NULL DEFAULT 0"),
                ("USED",       "TINYINT(1) DEFAULT 0"),
                ("CREATED_AT", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
            ],
        ),
        (
            "SZEROS",
            """CREATE TABLE IF NOT EXISTS SZEROS (
                ID INT AUTO_INCREMENT PRIMARY KEY,
                USER_ID VARCHAR(20) NOT NULL,
                COIN VARCHAR(64) UNIQUE NOT NULL,
                COINVALUE BIGINT NOT NULL DEFAULT 0,
                FOREIGN KEY (USER_ID) REFERENCES NAMES(USER_ID) ON DELETE CASCADE
            ) ENGINE=InnoDB""",
            [
                ("USER_ID",   "VARCHAR(20) NOT NULL DEFAULT ''"),
                ("COIN",      "VARCHAR(64) NOT NULL DEFAULT ''"),
                ("COINVALUE", "BIGINT NOT NULL DEFAULT 0"),
            ],
        ),
        (
            "TRANSACTIONS",
            """CREATE TABLE IF NOT EXISTS TRANSACTIONS (
                ID INT AUTO_INCREMENT PRIMARY KEY,
                PAYER_CODE_NAME VARCHAR(20) NOT NULL,
                RECEIVER_CODE_NAME VARCHAR(20) NOT NULL,
                PAYER_COIN VARCHAR(64) NOT NULL,
                RECEIVER_COIN VARCHAR(64) NOT NULL,
                AMOUNT BIGINT NOT NULL,
                CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB""",
            [
                ("PAYER_CODE_NAME",    "VARCHAR(20) NOT NULL DEFAULT ''"),
                ("RECEIVER_CODE_NAME", "VARCHAR(20) NOT NULL DEFAULT ''"),
                ("PAYER_COIN",         "VARCHAR(64) NOT NULL DEFAULT ''"),
                ("RECEIVER_COIN",      "VARCHAR(64) NOT NULL DEFAULT ''"),
                ("AMOUNT",             "BIGINT NOT NULL DEFAULT 0"),
                ("CREATED_AT",         "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
            ],
        ),
        (
            "RUN_SESSIONS",
            """CREATE TABLE IF NOT EXISTS RUN_SESSIONS (
                ID INT AUTO_INCREMENT PRIMARY KEY,
                USER_ID VARCHAR(20) NOT NULL,
                DISTANCE_KM DECIMAL(10,3) NOT NULL DEFAULT 0,
                COINS_EARNED INT NOT NULL DEFAULT 0,
                CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (USER_ID) REFERENCES NAMES(USER_ID) ON DELETE CASCADE
            ) ENGINE=InnoDB""",
            [
                ("USER_ID",      "VARCHAR(20) NOT NULL DEFAULT ''"),
                ("DISTANCE_KM",  "DECIMAL(10,3) NOT NULL DEFAULT 0"),
                ("COINS_EARNED", "INT NOT NULL DEFAULT 0"),
                ("CREATED_AT",   "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
            ],
        ),
        (
            "STORE_PRODUCTS",
            """CREATE TABLE IF NOT EXISTS STORE_PRODUCTS (
                ID INT AUTO_INCREMENT PRIMARY KEY,
                NAME VARCHAR(120) NOT NULL,
                SLUG VARCHAR(120) UNIQUE NOT NULL,
                PRICE_COINS DECIMAL(10,4) NOT NULL DEFAULT 1.0,
                PRICE_INR INT NOT NULL DEFAULT 10000,
                DESCRIPTION TEXT,
                FEATURES TEXT,
                IMAGE_URL VARCHAR(500) DEFAULT '',
                CATEGORY VARCHAR(60) DEFAULT 'watches',
                STOCK INT NOT NULL DEFAULT 10,
                ACTIVE TINYINT(1) DEFAULT 1,
                CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UPDATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                COINS_ENABLED TINYINT(1) DEFAULT 1,
                INR_ENABLED TINYINT(1) DEFAULT 1
            ) ENGINE=InnoDB""",
            [
                ("NAME",          "VARCHAR(120) NOT NULL DEFAULT ''"),
                ("SLUG",          "VARCHAR(120) NOT NULL DEFAULT ''"),
                ("PRICE_COINS",   "DECIMAL(10,4) NOT NULL DEFAULT 1.0"),
                ("PRICE_INR",     "INT NOT NULL DEFAULT 10000"),
                ("DESCRIPTION",   "TEXT"),
                ("FEATURES",      "TEXT"),
                ("IMAGE_URL",     "VARCHAR(500) DEFAULT ''"),
                ("CATEGORY",      "VARCHAR(60) DEFAULT 'watches'"),
                ("STOCK",         "INT NOT NULL DEFAULT 10"),
                ("ACTIVE",        "TINYINT(1) DEFAULT 1"),
                ("CREATED_AT",    "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
                ("UPDATED_AT",    "TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
                ("COINS_ENABLED", "TINYINT(1) DEFAULT 1"),
                ("INR_ENABLED",   "TINYINT(1) DEFAULT 1"),
            ],
        ),
        (
            "STORE_ORDERS",
            """CREATE TABLE IF NOT EXISTS STORE_ORDERS (
                ID INT AUTO_INCREMENT PRIMARY KEY,
                ORDER_NO VARCHAR(40) UNIQUE NOT NULL,
                USER_ID VARCHAR(20) NOT NULL,
                CODE_NAME VARCHAR(20) NOT NULL,
                PRODUCT_ID INT NOT NULL,
                PRODUCT_NAME VARCHAR(120) NOT NULL,
                PAYMENT_METHOD VARCHAR(20) NOT NULL,
                COINS_SPENT DECIMAL(10,4) DEFAULT 0,
                INR_PAID INT DEFAULT 0,
                RAZORPAY_ORDER_ID VARCHAR(64) DEFAULT NULL,
                RAZORPAY_PAYMENT_ID VARCHAR(64) DEFAULT NULL,
                STATUS VARCHAR(20) DEFAULT 'PENDING',
                SHIPPING_NAME VARCHAR(80) DEFAULT '',
                SHIPPING_ADDRESS TEXT,
                SHIPPING_PHONE VARCHAR(20) DEFAULT '',
                CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                REFUND_COINS DECIMAL(12,4) DEFAULT 0,
                REFUNDED_AT TIMESTAMP NULL DEFAULT NULL,
                FOREIGN KEY (USER_ID) REFERENCES NAMES(USER_ID) ON DELETE CASCADE
            ) ENGINE=InnoDB""",
            [
                ("ORDER_NO",            "VARCHAR(40) NOT NULL DEFAULT ''"),
                ("USER_ID",             "VARCHAR(20) NOT NULL DEFAULT ''"),
                ("CODE_NAME",           "VARCHAR(20) NOT NULL DEFAULT ''"),
                ("PRODUCT_ID",          "INT NOT NULL DEFAULT 0"),
                ("PRODUCT_NAME",        "VARCHAR(120) NOT NULL DEFAULT ''"),
                ("PAYMENT_METHOD",      "VARCHAR(20) NOT NULL DEFAULT ''"),
                ("COINS_SPENT",         "DECIMAL(10,4) DEFAULT 0"),
                ("INR_PAID",            "INT DEFAULT 0"),
                ("RAZORPAY_ORDER_ID",   "VARCHAR(64) DEFAULT NULL"),
                ("RAZORPAY_PAYMENT_ID", "VARCHAR(64) DEFAULT NULL"),
                ("STATUS",              "VARCHAR(20) DEFAULT 'PENDING'"),
                ("SHIPPING_NAME",       "VARCHAR(80) DEFAULT ''"),
                ("SHIPPING_ADDRESS",    "TEXT"),
                ("SHIPPING_PHONE",      "VARCHAR(20) DEFAULT ''"),
                ("CREATED_AT",          "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
                ("REFUND_COINS",        "DECIMAL(12,4) DEFAULT 0"),
                ("REFUNDED_AT",         "TIMESTAMP NULL DEFAULT NULL"),
            ],
        ),
        (
            "STORE_SETTINGS",
            """CREATE TABLE IF NOT EXISTS STORE_SETTINGS (
                SKEY VARCHAR(60) PRIMARY KEY,
                SVAL TEXT NOT NULL,
                UPDATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB""",
            [
                ("SVAL",       "TEXT NOT NULL"),
                ("UPDATED_AT", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
            ],
        ),
        (
            "COIN_PURCHASES",
            """CREATE TABLE IF NOT EXISTS COIN_PURCHASES (
                ID INT AUTO_INCREMENT PRIMARY KEY,
                USER_ID VARCHAR(20) NOT NULL,
                CODE_NAME VARCHAR(20) NOT NULL,
                RAZORPAY_ORDER_ID VARCHAR(64),
                RAZORPAY_PAYMENT_ID VARCHAR(64),
                COINS INT NOT NULL,
                AMOUNT_PAISE INT NOT NULL,
                STATUS VARCHAR(20) DEFAULT 'PENDING',
                INVOICE_NO VARCHAR(40),
                CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (USER_ID) REFERENCES NAMES(USER_ID) ON DELETE CASCADE
            ) ENGINE=InnoDB""",
            [
                ("USER_ID",             "VARCHAR(20) NOT NULL DEFAULT ''"),
                ("CODE_NAME",           "VARCHAR(20) NOT NULL DEFAULT ''"),
                ("RAZORPAY_ORDER_ID",   "VARCHAR(64)"),
                ("RAZORPAY_PAYMENT_ID", "VARCHAR(64)"),
                ("COINS",               "INT NOT NULL DEFAULT 0"),
                ("AMOUNT_PAISE",        "INT NOT NULL DEFAULT 0"),
                ("STATUS",              "VARCHAR(20) DEFAULT 'PENDING'"),
                ("INVOICE_NO",          "VARCHAR(40)"),
                ("CREATED_AT",          "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
            ],
        ),
        (
            "PRODUCT_IMAGES",
            """CREATE TABLE IF NOT EXISTS PRODUCT_IMAGES (
                ID INT AUTO_INCREMENT PRIMARY KEY,
                FILENAME VARCHAR(200) NOT NULL,
                MIME_TYPE VARCHAR(50) NOT NULL DEFAULT 'image/jpeg',
                FILE_SIZE INT DEFAULT 0,
                IMAGE_DATA LONGBLOB NOT NULL,
                CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB""",
            [
                ("FILENAME",   "VARCHAR(200) NOT NULL DEFAULT ''"),
                ("MIME_TYPE",  "VARCHAR(50) NOT NULL DEFAULT 'image/jpeg'"),
                ("FILE_SIZE",  "INT DEFAULT 0"),
                ("IMAGE_DATA", "LONGBLOB NOT NULL"),
                ("CREATED_AT", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
            ],
        ),
        (
            "USER_PROFILES",
            """CREATE TABLE IF NOT EXISTS USER_PROFILES (
                USER_ID VARCHAR(20) PRIMARY KEY,
                BIO VARCHAR(500) DEFAULT '',
                WORK_TITLE VARCHAR(120) DEFAULT '',
                WORK_COMPANY VARCHAR(120) DEFAULT '',
                WORK_LOCATION VARCHAR(120) DEFAULT '',
                PHOTO_URL VARCHAR(500) DEFAULT '',
                IS_PUBLIC TINYINT(1) DEFAULT 1,
                UPDATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                FOREIGN KEY (USER_ID) REFERENCES NAMES(USER_ID) ON DELETE CASCADE
            ) ENGINE=InnoDB""",
            [
                ("BIO",           "VARCHAR(500) DEFAULT ''"),
                ("WORK_TITLE",    "VARCHAR(120) DEFAULT ''"),
                ("WORK_COMPANY",  "VARCHAR(120) DEFAULT ''"),
                ("WORK_LOCATION", "VARCHAR(120) DEFAULT ''"),
                ("PHOTO_URL",     "VARCHAR(500) DEFAULT ''"),
                ("IS_PUBLIC",     "TINYINT(1) DEFAULT 1"),
                ("UPDATED_AT",    "TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
            ],
        ),
        (
            "CONNECTIONS",
            """CREATE TABLE IF NOT EXISTS CONNECTIONS (
                ID INT AUTO_INCREMENT PRIMARY KEY,
                REQUESTER_ID VARCHAR(20) NOT NULL,
                RECEIVER_ID VARCHAR(20) NOT NULL,
                STATUS VARCHAR(20) DEFAULT 'PENDING',
                CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UPDATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY uq_conn (REQUESTER_ID, RECEIVER_ID),
                FOREIGN KEY (REQUESTER_ID) REFERENCES NAMES(USER_ID) ON DELETE CASCADE,
                FOREIGN KEY (RECEIVER_ID)  REFERENCES NAMES(USER_ID) ON DELETE CASCADE
            ) ENGINE=InnoDB""",
            [
                ("REQUESTER_ID", "VARCHAR(20) NOT NULL DEFAULT ''"),
                ("RECEIVER_ID",  "VARCHAR(20) NOT NULL DEFAULT ''"),
                ("STATUS",       "VARCHAR(20) DEFAULT 'PENDING'"),
                ("UPDATED_AT",   "TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
            ],
        ),
        (
            "BROADCASTS",
            """CREATE TABLE IF NOT EXISTS BROADCASTS (
                ID VARCHAR(40) PRIMARY KEY,
                TITLE VARCHAR(200) NOT NULL,
                BODY TEXT NOT NULL,
                SENT_BY VARCHAR(20) NOT NULL DEFAULT 'Admin',
                CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB""",
            [
                ("TITLE",      "VARCHAR(200) NOT NULL DEFAULT ''"),
                ("BODY",       "TEXT NOT NULL"),
                ("SENT_BY",    "VARCHAR(20) NOT NULL DEFAULT 'Admin'"),
                ("CREATED_AT", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
            ],
        ),
        (
            "STORE_CATEGORIES",
            """CREATE TABLE IF NOT EXISTS STORE_CATEGORIES (
                ID INT AUTO_INCREMENT PRIMARY KEY,
                NAME VARCHAR(60) NOT NULL,
                SLUG VARCHAR(60) UNIQUE NOT NULL,
                CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB""",
            [
                ("NAME",       "VARCHAR(60) NOT NULL DEFAULT ''"),
                ("SLUG",       "VARCHAR(60) NOT NULL DEFAULT ''"),
                ("CREATED_AT", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
            ],
        ),
        (
            "ADMIN_COIN_LOG",
            """CREATE TABLE IF NOT EXISTS ADMIN_COIN_LOG (
                ID INT AUTO_INCREMENT PRIMARY KEY,
                ADMIN_CODE_NAME VARCHAR(20) NOT NULL,
                TARGET_CODE_NAME VARCHAR(20) NOT NULL,
                AMOUNT DECIMAL(10,4) NOT NULL,
                NOTE VARCHAR(200) DEFAULT '',
                CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB""",
            [
                ("ADMIN_CODE_NAME",  "VARCHAR(20) NOT NULL DEFAULT ''"),
                ("TARGET_CODE_NAME", "VARCHAR(20) NOT NULL DEFAULT ''"),
                ("AMOUNT",           "DECIMAL(10,4) NOT NULL DEFAULT 0"),
                ("NOTE",             "VARCHAR(200) DEFAULT ''"),
                ("CREATED_AT",       "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
            ],
        ),
    ]

    @classmethod
    def _create_tables(cls) -> None:
        conn = cls.get_connection()
        cur  = conn.cursor(buffered=True)  # buffered=True prevents "Unread result found" errors
        try:
            for table_name, create_sql, columns in cls._SCHEMA:
                cur.execute(create_sql)
                for col_name, col_def in columns:
                    cls._add_column_if_missing(cur, table_name, col_name, col_def)

            # Seed default settings
            for key, val in [
                ("coin_to_inr",          "10000"),
                ("service_charge_inr",   "500"),
                ("service_charge_coins", "0.05"),
                ("store_name",           "NOVA WATCHES"),
                ("razorpay_enabled",     "1"),
                ("coins_enabled",        "1"),
            ]:
                cur.execute(
                    "INSERT IGNORE INTO STORE_SETTINGS (SKEY, SVAL) VALUES (%s, %s)",
                    (key, val),
                )
            # NOTE: Default categories are NOT seeded here so admin deletions are permanent.
            # On a brand-new blank DB (no categories at all) we seed one safe default.
            cur.execute("SELECT COUNT(*) FROM STORE_CATEGORIES")
            if (cur.fetchone() or [0])[0] == 0:
                cur.execute("INSERT IGNORE INTO STORE_CATEGORIES (NAME, SLUG) VALUES ('Watches', 'watches')")
            conn.commit()
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def _add_column_if_missing(cur, table: str, column: str, definition: str) -> None:
        """
        Add a column only if it does not already exist.
        Uses INFORMATION_SCHEMA — never fails on existing columns.
        """
        try:
            cur.execute(
                """SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS
                   WHERE TABLE_SCHEMA = DATABASE()
                     AND TABLE_NAME   = %s
                     AND COLUMN_NAME  = %s""",
                (table, column),
            )
            row = cur.fetchone()
            try:
                cur.fetchall()  # drain any remaining results to prevent "Unread result found"
            except Exception:
                pass
            if row and row[0] == 0:
                cur.execute(f"ALTER TABLE `{table}` ADD COLUMN `{column}` {definition}")
        except Exception as exc:
            logging.warning("[schema] Could not add %s.%s: %s", table, column, exc)


# =============================================================================
# QR MANAGER
# =============================================================================

class QRManager:
    """Compact HMAC-based QR code generation and verification.

    Binary payload layout (then base64url-encoded, no padding):
      [1 byte : name_len]
      [N bytes: code_name utf-8]
      [1 byte : coin_len]
      [M bytes: coin string utf-8]
      [4 bytes: unix timestamp uint32 big-endian]
      [8 bytes: HMAC-SHA256 first 8 bytes]

    Typical output length: ~44 chars  (was ~248 chars with Fernet).
    Shorter string → lower QR version → much faster camera scan.
    Security: HMAC-SHA256 with truncation to 64 bits is standard practice
    for short-lived tokens (RFC 4226 / TOTP uses 31 bits).
    """

    _QR_TTL = 60  # seconds, same as before

    def __init__(self, fernet_key: str) -> None:
        # Derive a 32-byte HMAC key from the existing Fernet key.
        # No .env change needed — same QR_FERNET_KEY env var works.
        raw = fernet_key.encode() if isinstance(fernet_key, str) else fernet_key
        self._secret = hashlib.sha256(raw).digest()

    # ------------------------------------------------------------------ #
    #  internal helpers                                                    #
    # ------------------------------------------------------------------ #

    def _sign(self, data: bytes) -> bytes:
        """Return first 8 bytes of HMAC-SHA256(secret, data)."""
        return hmac.new(self._secret, data, hashlib.sha256).digest()[:8]

    @staticmethod
    def _pack(code_name: str, coin: str, timestamp: int) -> bytes:
        name_b = code_name.encode("utf-8")
        coin_b = coin.encode("utf-8")
        if len(name_b) > 255 or len(coin_b) > 255:
            raise ValueError("code_name / coin too long for QR payload")
        return (
            struct.pack("!B", len(name_b))
            + name_b
            + struct.pack("!B", len(coin_b))
            + coin_b
            + struct.pack("!I", timestamp)
        )

    @staticmethod
    def _unpack(body: bytes) -> tuple:
        name_len = body[0]
        name     = body[1 : 1 + name_len].decode("utf-8")
        offset   = 1 + name_len
        coin_len = body[offset]
        coin     = body[offset + 1 : offset + 1 + coin_len].decode("utf-8")
        offset  += 1 + coin_len
        ts,      = struct.unpack("!I", body[offset : offset + 4])
        return name, coin, ts

    # ------------------------------------------------------------------ #
    #  public API (same signatures as before)                              #
    # ------------------------------------------------------------------ #

    def generate_payload(self, code_name: str, coin: str) -> str:
        timestamp = int(time.time())
        body      = self._pack(code_name, coin, timestamp)
        mac       = self._sign(body)
        return base64.urlsafe_b64encode(body + mac).decode().rstrip("=")

    def decrypt_payload(self, qr_string: str) -> Optional[dict]:
        try:
            # Restore stripped base64 padding
            padded = qr_string + "=" * (-len(qr_string) % 4)
            raw    = base64.urlsafe_b64decode(padded)

            # Minimum bytes: 1(len) + 1(name) + 4(coin) + 4(ts) + 8(mac) = 18
            if len(raw) < 18:
                return None

            body, mac = raw[:-8], raw[-8:]

            # Constant-time MAC verification
            if not hmac.compare_digest(mac, self._sign(body)):
                logging.warning("[QR Decrypt] MAC mismatch")
                return None

            code_name, coin, timestamp = self._unpack(body)

            if int(time.time()) - timestamp > self._QR_TTL:
                return None

            return {
                "code_name": code_name,
                "coin":      coin,
                "timestamp": timestamp,
                "nonce":     "",  # kept for API compatibility
                "token":     "",
            }
        except Exception as exc:
            logging.warning("[QR Decrypt] %s: %s", type(exc).__name__, exc)
            return None


# =============================================================================
# ANTI-CHEAT VALIDATOR
# =============================================================================

class RunValidator:
    """Server-side GPS track validation."""

    @staticmethod
    def _haversine_km(lat1, lon1, lat2, lon2) -> float:
        R    = 6371.0
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        a    = (
            math.sin(dlat / 2) ** 2
            + math.cos(math.radians(lat1))
            * math.cos(math.radians(lat2))
            * math.sin(dlon / 2) ** 2
        )
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    @staticmethod
    def _ist_time(unix_ts: float) -> dtime:
        return datetime.utcfromtimestamp(unix_ts + IST_OFFSET_SEC).time()

    @classmethod
    def validate(cls, points: list) -> tuple:
        if not points or len(points) < 2:
            return 0.0, "Minimum 2 GPS points required"
        try:
            pts = sorted(points, key=lambda p: float(p["timestamp"]))
        except (KeyError, TypeError, ValueError):
            return 0.0, "Invalid point data: timestamp missing"

        first_time = cls._ist_time(float(pts[0]["timestamp"]))
        if first_time >= dtime(RUN_WINDOW_START_HOUR, 0, 0):
            return 0.0, (
                f"Register BEFORE {RUN_WINDOW_START_HOUR}:00 AM IST. "
                f"First point was {first_time.strftime('%H:%M:%S')} IST."
            )

        total_km: float     = 0.0
        segment_speeds: list = []

        for i in range(1, len(pts)):
            prev, curr = pts[i - 1], pts[i]
            try:
                lat1, lon1 = float(prev["lat"]), float(prev["lon"])
                lat2, lon2 = float(curr["lat"]), float(curr["lon"])
                t1, t2     = float(prev["timestamp"]), float(curr["timestamp"])
            except (KeyError, TypeError, ValueError):
                return 0.0, f"Invalid GPS data at point {i}"

            pt_time = cls._ist_time(t2)
            if not (dtime(RUN_WINDOW_START_HOUR, 0, 0) <= pt_time <= dtime(RUN_WINDOW_END_HOUR, 0, 0)):
                return 0.0, (
                    f"Point {i} outside window "
                    f"({RUN_WINDOW_START_HOUR}:00–{RUN_WINDOW_END_HOUR}:00 AM IST). "
                    f"Got {pt_time.strftime('%H:%M:%S')} IST."
                )

            acc = curr.get("accuracy_m")
            if acc is not None:
                try:
                    if float(acc) > MAX_GPS_ACCURACY_METERS:
                        return 0.0, f"GPS accuracy too low at point {i} ({acc}m)."
                except (TypeError, ValueError):
                    pass

            dt_hours = (t2 - t1) / 3600.0
            if dt_hours <= 0:
                return 0.0, "Timestamps must be strictly increasing"

            seg_km    = cls._haversine_km(lat1, lon1, lat2, lon2)
            speed_kmh = seg_km / dt_hours

            if speed_kmh > MAX_RUNNING_SPEED_KMH:
                return 0.0, (
                    f"Speed too high ({speed_kmh:.1f} km/h) at segment {i}. "
                    f"Max: {MAX_RUNNING_SPEED_KMH} km/h."
                )
            if speed_kmh >= MIN_RUNNING_SPEED_KMH:
                total_km += seg_km
                segment_speeds.append(speed_kmh)

        if len(segment_speeds) >= 5:
            mean = sum(segment_speeds) / len(segment_speeds)
            std  = math.sqrt(
                sum((s - mean) ** 2 for s in segment_speeds) / len(segment_speeds)
            )
            if std < SPEED_VARIANCE_MIN_KMPH:
                return 0.0, (
                    f"Speed too constant (std-dev {std:.2f} km/h). "
                    f"Minimum: {SPEED_VARIANCE_MIN_KMPH} km/h."
                )
        return total_km, None


# =============================================================================
# OTP SERVICE
# =============================================================================

class EmailTemplates:
    """
    Single source-of-truth for all transactional email HTML.
    All values come from Config (which reads from .env).
    """

    @staticmethod
    def build(title: str, subtitle: str, body_html: str, footer_note: str = "") -> str:
        """
        Wrap any email content in the standard Nova branded shell.
        - title      : e.g. "PLEASE VERIFY YOUR IDENTITY"
        - subtitle   : e.g. "HERE IS YOUR NOVA AUTHENTICATION CODE"
        - body_html  : inner HTML between the dividers
        - footer_note: optional small note below final divider
        """
        co   = Config.COMPANY_NAME
        url  = Config.WEBSITE_URL
        sup  = Config.SUPPORT_EMAIL
        logo = Config.LOGO_URL
        fn   = footer_note or (
            f"You're receiving this email from {co}. "
            f"If this wasn't you, please ignore this email."
        )
        return f"""
<html>
<body style="margin:0;padding:0;background:#ffffff;">
  <table width="100%" cellpadding="0" cellspacing="0">
    <tr>
      <td align="center" style="padding:40px 20px;">
        <table width="560" cellpadding="0" cellspacing="0" style="padding:40px;">
          <tr>
            <td align="center" style="padding-bottom:12px;">
              <img src="{logo}" width="130" alt="{co}">
            </td>
          </tr>
          <tr>
            <td align="center"
                style="font-family:Arial,sans-serif;font-size:20px;
                       font-weight:bold;letter-spacing:2px;padding-bottom:6px;">
              {title}
            </td>
          </tr>
          <tr>
            <td align="center"
                style="font-family:Arial,sans-serif;font-size:12px;
                       letter-spacing:1px;color:#333;padding-bottom:20px;">
              {subtitle}
            </td>
          </tr>
          <tr><td style="border-top:1px solid #000;padding-bottom:20px;"></td></tr>
          <tr>
            <td style="font-family:'Courier New',monospace;font-size:13px;line-height:1.8;">
              {body_html}
            </td>
          </tr>
          <tr><td style="border-top:1px solid #000;padding:16px 0;"></td></tr>
          <tr>
            <td align="center"
                style="font-family:Arial,sans-serif;font-size:11px;color:#555;line-height:1.7;">
              {fn}<br><br>
              <a href="mailto:{sup}" style="color:#000;text-decoration:none;">{sup}</a>
              &nbsp;&nbsp;|&nbsp;&nbsp;
              <a href="{url}" style="color:#000;text-decoration:none;">{url}</a>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

    @staticmethod
    def otp(otp_code: str) -> str:
        co  = Config.COMPANY_NAME
        body = f"""
Your <b>{co}</b> authentication code is:<br><br>
<div style="font-family:'Courier New',monospace;font-size:36px;font-weight:bold;
            letter-spacing:6px;text-align:center;padding:16px 0;">{otp_code}</div>
<br>
This code is valid for 5 minutes and can only be used once.<br><br>
Please don't share this code with anyone — we'll never ask for it on the phone or via email.<br><br>
Thanks,<br>The {co} Team"""
        return EmailTemplates.build(
            title    = "PLEASE VERIFY YOUR IDENTITY",
            subtitle = f"HERE IS YOUR {co.upper()} AUTHENTICATION CODE",
            body_html= body,
            footer_note = (
                f"You're receiving this email because a verification code was requested "
                f"for your {co} account. If this wasn't you, please ignore this email."
            ),
        )

    @staticmethod
    def order_confirmed(order: dict) -> str:
        co          = Config.COMPANY_NAME
        order_no    = order.get("order_no", "")
        prod_name   = order.get("product_name", co)
        cust_name   = order.get("shipping_name") or order.get("customer_name", "Customer")
        pay_method  = (order.get("payment_method", "RAZORPAY")).upper()
        if pay_method == "COIN":
            total_str = f"&#x20b9;(Coins) {float(order.get('coins_spent', 0)):.4f}"
        else:
            total_str = f"Rs.&nbsp;{int(order.get('inr_paid', 0)):,}"

        body = f"""
Dear <b>{cust_name}</b>,<br><br>
Thank you for your order! We have successfully received your payment and your order is now confirmed.<br><br>
<table width="100%" cellpadding="0" cellspacing="0"
       style="font-family:'Courier New',monospace;font-size:13px;color:#111;">
  <tr><td style="padding:6px 0;">Order / Bill No</td>
      <td align="right" style="padding:6px 0;font-weight:bold;">{order_no}</td></tr>
  <tr><td style="padding:6px 0;">Product</td>
      <td align="right" style="padding:6px 0;">{prod_name}</td></tr>
  <tr><td style="padding:6px 0;">Total Paid</td>
      <td align="right" style="padding:6px 0;font-weight:bold;">{total_str}</td></tr>
  <tr><td style="padding:6px 0;">Payment Status</td>
      <td align="right" style="padding:6px 0;font-weight:bold;">PAID &#x2713;</td></tr>
</table><br>
Your invoice will be sent to you once your order is delivered.<br><br>
If you have any questions, feel free to contact our support team.<br><br>
Thanks,<br>The {co} Team"""

        return EmailTemplates.build(
            title     = "ORDER CONFIRMED",
            subtitle  = f"THANK YOU FOR SHOPPING WITH {co.upper()}",
            body_html = body,
            footer_note = (
                f"You're receiving this email because an order was placed on your "
                f"{co} account."
            ),
        )

    @staticmethod
    def order_delivered(order: dict) -> str:
        co          = Config.COMPANY_NAME
        order_no    = order.get("order_no", "")
        prod_name   = order.get("product_name", co)
        cust_name   = order.get("shipping_name") or order.get("customer_name", "Customer")
        pay_method  = (order.get("payment_method", "RAZORPAY")).upper()
        if pay_method == "COIN":
            total_str = f"&#x20b9;(Coins) {float(order.get('coins_spent', 0)):.4f}"
        else:
            total_str = f"Rs.&nbsp;{int(order.get('inr_paid', 0)):,}"

        body = f"""
Dear <b>{cust_name}</b>,<br><br>
Great news! Your order has been delivered. Please find your invoice attached to this email.<br><br>
<table width="100%" cellpadding="0" cellspacing="0"
       style="font-family:'Courier New',monospace;font-size:13px;color:#111;">
  <tr><td style="padding:6px 0;">Order / Bill No</td>
      <td align="right" style="padding:6px 0;font-weight:bold;">{order_no}</td></tr>
  <tr><td style="padding:6px 0;">Product</td>
      <td align="right" style="padding:6px 0;">{prod_name}</td></tr>
  <tr><td style="padding:6px 0;">Total Paid</td>
      <td align="right" style="padding:6px 0;font-weight:bold;">{total_str}</td></tr>
  <tr><td style="padding:6px 0;">Payment Status</td>
      <td align="right" style="padding:6px 0;font-weight:bold;">PAID &#x2713;</td></tr>
</table><br>
We hope you love your purchase! If you have any questions or concerns, please reach out to our support team.<br><br>
Thanks,<br>The {co} Team"""

        return EmailTemplates.build(
            title     = "ORDER DELIVERED",
            subtitle  = f"YOUR INVOICE IS ATTACHED",
            body_html = body,
            footer_note = (
                f"You're receiving this email because your {co} order has been delivered."
            ),
        )

    @staticmethod
    def coin_cash_admin(code_name: str, user_full_name: str, user_email: str,
                        user_phone: str, coin_account: str, coins_float: float,
                        gross_inr: float, deduction_inr: float, net_inr: float,
                        payment_block: str, req_time: str) -> str:
        co   = Config.COMPANY_NAME
        body = f"""
<b>New Conversion Request — Action Required</b><br><br>
<table width="100%" cellpadding="0" cellspacing="0"
       style="font-family:'Courier New',monospace;font-size:13px;color:#111;">
  <tr><td style="padding:5px 0;color:#555;">Request Time</td>
      <td align="right" style="padding:5px 0;">{req_time}</td></tr>
  <tr><td style="padding:5px 0;color:#555;">User Code Name</td>
      <td align="right" style="padding:5px 0;font-weight:bold;">{code_name}</td></tr>
  <tr><td style="padding:5px 0;color:#555;">Full Name</td>
      <td align="right" style="padding:5px 0;">{user_full_name}</td></tr>
  <tr><td style="padding:5px 0;color:#555;">Email</td>
      <td align="right" style="padding:5px 0;">{user_email or 'N/A'}</td></tr>
  <tr><td style="padding:5px 0;color:#555;">Phone</td>
      <td align="right" style="padding:5px 0;">{user_phone or 'N/A'}</td></tr>
  <tr><td style="padding:5px 0;color:#555;">Nova Account No.</td>
      <td align="right" style="padding:5px 0;">{coin_account}</td></tr>
  <tr><td colspan="2" style="padding:8px 0;"><hr style="border:none;border-top:1px solid #eee;"/></td></tr>
  <tr><td style="padding:5px 0;color:#555;">Coins to Convert</td>
      <td align="right" style="padding:5px 0;font-weight:bold;">{coins_float} Coin{'s' if coins_float != 1 else ''}</td></tr>
  <tr><td style="padding:5px 0;color:#555;">Rate (1 Coin = Rs.10,000)</td>
      <td align="right" style="padding:5px 0;">Rs.{gross_inr:,.0f} (gross)</td></tr>
  <tr><td style="padding:5px 0;color:#555;">10% Service Charge</td>
      <td align="right" style="padding:5px 0;">- Rs.{deduction_inr:,.0f}</td></tr>
  <tr><td style="padding:5px 0;font-weight:bold;">Net Amount to Pay</td>
      <td align="right" style="padding:5px 0;font-weight:bold;">Rs.{net_inr:,.0f}</td></tr>
  <tr><td colspan="2" style="padding:8px 0;"><hr style="border:none;border-top:1px solid #eee;"/></td></tr>
  {payment_block}
</table><br>
&#x26A0; User has explicitly agreed to the 10% deduction.
Please verify balance and process within 2-3 working days."""

        return EmailTemplates.build(
            title     = "COIN TO CASH — REQUEST",
            subtitle  = f"{co.upper()} CONVERSION REQUEST",
            body_html = body,
            footer_note = f"{co} System — action required by admin.",
        )

    @staticmethod
    def coin_cash_user(code_name: str, coins_float: float, gross_inr: float,
                       deduction_inr: float, net_inr: float) -> str:
        co   = Config.COMPANY_NAME
        body = f"""
Hi <b>{code_name}</b>,<br><br>
Your coin-to-cash conversion request has been received and will be processed
within <b>2–3 working days</b>.<br><br>
<table width="100%" cellpadding="0" cellspacing="0"
       style="font-family:'Courier New',monospace;font-size:13px;color:#111;">
  <tr><td style="padding:5px 0;color:#555;">Coins Requested</td>
      <td align="right" style="padding:5px 0;font-weight:bold;">{coins_float} Coin{'s' if coins_float != 1 else ''}</td></tr>
  <tr><td style="padding:5px 0;color:#555;">Gross Value</td>
      <td align="right" style="padding:5px 0;">Rs.{gross_inr:,.0f}</td></tr>
  <tr><td style="padding:5px 0;color:#555;">10% Service Charge</td>
      <td align="right" style="padding:5px 0;">- Rs.{deduction_inr:,.0f}</td></tr>
  <tr><td style="padding:5px 0;font-weight:bold;">Net Amount You Receive</td>
      <td align="right" style="padding:5px 0;font-weight:bold;">Rs.{net_inr:,.0f}</td></tr>
</table><br>
If you have any questions, feel free to contact us.<br><br>
Thanks,<br>The {co} Team"""

        return EmailTemplates.build(
            title     = "CONVERSION REQUEST RECEIVED",
            subtitle  = f"{co.upper()} — CASH WITHDRAWAL",
            body_html = body,
            footer_note = f"You're receiving this because you submitted a coin-to-cash request on {co}.",
        )

    @staticmethod
    def broadcast_notification(code_name: str, notif_title: str, notif_body: str) -> str:
        co   = Config.COMPANY_NAME
        body = f"""
Hi <b>{code_name}</b>,<br><br>
<b>{notif_title}</b><br><br>
{notif_body}<br><br>
Stay tuned for more updates.<br><br>
Thanks,<br>The {co} Team"""

        return EmailTemplates.build(
            title     = notif_title.upper(),
            subtitle  = f"MESSAGE FROM {co.upper()}",
            body_html = body,
            footer_note = (
                f"You're receiving this because you are registered on {co}. "
                f"This is an official broadcast notification."
            ),
        )

    @staticmethod
    def contact_confirmation(name: str, query_summary: str) -> str:
        co   = Config.COMPANY_NAME
        sup  = Config.SUPPORT_EMAIL
        body = f"""
Hi <b>{name}</b>,<br><br>
Thank you for reaching out to us. We have successfully received your query/request
and our team will get back to you as soon as possible.<br><br>
<table width="100%" cellpadding="0" cellspacing="0"
       style="font-family:'Courier New',monospace;font-size:13px;color:#111;">
  <tr><td style="padding:5px 0;color:#555;">Your Query</td>
      <td align="right" style="padding:5px 0;">{query_summary[:120]}{'...' if len(query_summary) > 120 else ''}</td></tr>
  <tr><td style="padding:5px 0;color:#555;">Status</td>
      <td align="right" style="padding:5px 0;font-weight:bold;">Received &#x2713;</td></tr>
</table><br>
If you need to follow up, please reply to this email or write to
<a href="mailto:{sup}">{sup}</a>.<br><br>
Thanks,<br>The {co} Team"""

        return EmailTemplates.build(
            title     = "QUERY RECEIVED",
            subtitle  = f"THANK YOU FOR CONTACTING {co.upper()}",
            body_html = body,
            footer_note = (
                f"You're receiving this because you submitted a contact/query request "
                f"on {co}."
            ),
        )



# =============================================================================
# OTP SERVICE
# =============================================================================

class OTPService:
    """OTP generation, storage, verification, and email delivery."""

    @staticmethod
    def _check_rate_limit(target: str, purpose: str) -> bool:
        """
        Return True if allowed to send.
        Blocks if 4 or more OTPs were already sent in the last 60 minutes.
        """
        conn = DatabaseManager.get_connection()
        cur  = conn.cursor()
        try:
            window_start = int(time.time()) - 3600
            cur.execute(
                """SELECT COUNT(*) FROM OTP_STORE
                   WHERE TARGET = %s
                     AND PURPOSE = %s
                     AND EXPIRES_AT >= %s""",
                (target, purpose, window_start),
            )
            row = cur.fetchone()
            count = int(row[0]) if row else 0
            return count < 4
        finally:
            cur.close()
            conn.close()

    @staticmethod
    def generate() -> str:
        return str(random.randint(100000, 999999))

    @staticmethod
    def store(target: str, otp: str, purpose: str) -> None:
        conn = DatabaseManager.get_connection()
        cur  = conn.cursor()
        try:
            cur.execute(
                "UPDATE OTP_STORE SET USED=1 WHERE TARGET=%s AND PURPOSE=%s AND USED=0",
                (target, purpose),
            )
            expires = int(time.time()) + OTP_EXPIRY_SEC
            cur.execute(
                "INSERT INTO OTP_STORE (TARGET, OTP_CODE, PURPOSE, EXPIRES_AT) VALUES (%s,%s,%s,%s)",
                (target, otp, purpose, expires),
            )
            conn.commit()
        finally:
            cur.close(); conn.close()

    @staticmethod
    def verify(target: str, otp: str, purpose: str) -> bool:
        conn = DatabaseManager.get_connection()
        cur  = conn.cursor()
        try:
            cur.execute(
                """SELECT ID FROM OTP_STORE
                   WHERE TARGET=%s AND OTP_CODE=%s AND PURPOSE=%s AND USED=0 AND EXPIRES_AT>%s
                   ORDER BY ID DESC LIMIT 1""",
                (target, otp, purpose, int(time.time())),
            )
            row = cur.fetchone()
            if not row:
                return False
            cur.execute("UPDATE OTP_STORE SET USED=1 WHERE ID=%s", (row[0],))
            conn.commit()
            return True
        finally:
            cur.close(); conn.close()

    @staticmethod
    def send_email(email: str, otp: str) -> None:
        if not Config.SMTP_USER or not Config.SMTP_PASSWORD:
            print(f"[DEV EMAIL OTP] {email} -> {otp}")
            return

        html = EmailTemplates.otp(otp)

        msg            = EmailMessage()
        msg["Subject"] = f"{Config.COMPANY_NAME} – Your Authentication Code"
        msg["From"]    = f"{Config.COMPANY_NAME} <{Config.SMTP_USER}>"
        msg["To"]      = email
        msg.set_content(f"Your {Config.COMPANY_NAME} OTP is: {otp}. Valid for 5 minutes. Do not share it.")
        msg.add_alternative(html, subtype="html")

        _smtp_send(msg)


# =============================================================================
# PAYMENT SERVICE
# =============================================================================

class PaymentService:
    """Razorpay order creation and signature verification."""

    @staticmethod
    def create_order(amount_paise: int, receipt: str) -> dict:
        import urllib.request, urllib.error
        if not Config.RAZORPAY_KEY_ID or not Config.RAZORPAY_KEY_SECRET:
            raise ValueError("Razorpay keys not configured in .env")
        url     = "https://api.razorpay.com/v1/orders"
        payload = json.dumps(
            {"amount": amount_paise, "currency": "INR", "receipt": receipt}
        ).encode()
        creds   = base64.b64encode(
            f"{Config.RAZORPAY_KEY_ID}:{Config.RAZORPAY_KEY_SECRET}".encode()
        ).decode()
        req = urllib.request.Request(
            url, data=payload,
            headers={"Authorization": f"Basic {creds}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            err_data = json.loads(body) if body else {}
            rz_msg = err_data.get("error", {}).get("description", body)
            raise RuntimeError(f"Razorpay API error ({e.code}): {rz_msg}")

    @staticmethod
    def verify_signature(order_id: str, payment_id: str, signature: str) -> bool:
        msg      = f"{order_id}|{payment_id}".encode()
        expected = hmac.new(
            Config.RAZORPAY_KEY_SECRET.encode(), msg, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)


# =============================================================================
# INVOICE SERVICE  —  PDF generation + email dispatch
# =============================================================================

class InvoiceService:
    """Generate Bill-of-Supply PDF and email it to the customer."""

    # ── Logo path (served from /images/logo.png) ────────────────────────────
    LOGO_PATH = os.path.join(os.path.dirname(__file__), "images", "logo.png")

    # ── Brand colors (gold / dark) ──────────────────────────────────────────
    GOLD  = (212/255, 175/255,  55/255)
    BLACK = (0.08, 0.08, 0.08)
    DARK  = (0.14, 0.14, 0.14)
    WHITE = (1, 1, 1)
    GREY  = (0.94, 0.94, 0.94)
    MUTED = (0.45, 0.45, 0.45)

    @classmethod
    def _pt(cls, mm: float) -> float:
        """mm → reportlab points."""
        return mm * 2.8346

    @classmethod
    def generate_pdf(cls, order: dict) -> bytes:
        """
        Build a Nova Watch Bill-of-Supply PDF in memory and return bytes.

        order keys:
            order_no, product_name, quantity, unit_price_inr, unit_price_coins,
            payment_method (COIN | RAZORPAY), coins_spent, inr_paid,
            service_charge_inr, service_charge_coins,
            customer_name, customer_email,
            shipping_name, shipping_address, shipping_phone,
            created_at (datetime str), bill_date (str, optional)
        """
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.pdfgen import canvas as rl_canvas
            from reportlab.lib.utils import ImageReader
            from reportlab.platypus import Table, TableStyle
            from reportlab.lib import colors
        except ImportError:
            raise RuntimeError("reportlab not installed. Run: pip install reportlab")

        buf    = io.BytesIO()
        W, H   = A4          # 595 x 842 pts
        c      = rl_canvas.Canvas(buf, pagesize=A4)
        p      = cls._pt     # shorthand

        def setfill(col): c.setFillColorRGB(*col)
        def setstroke(col): c.setStrokeColorRGB(*col)

        # ── Header background ────────────────────────────────────────────────
        setfill(cls.BLACK)
        c.rect(0, H - p(42), W, p(42), fill=1, stroke=0)

        # ── Logo ─────────────────────────────────────────────────────────────
        if os.path.isfile(cls.LOGO_PATH):
            try:
                logo = ImageReader(cls.LOGO_PATH)
                c.drawImage(logo, p(6), H - p(38), width=p(28), height=p(28),
                            preserveAspectRatio=True, mask="auto")
            except Exception:
                pass

        # ── Brand name ───────────────────────────────────────────────────────
        setfill(cls.GOLD)
        c.setFont("Helvetica-Bold", 20)
        c.drawString(p(38), H - p(16), Config.COMPANY_NAME.upper())
        setfill(cls.WHITE)
        c.setFont("Helvetica", 8)
        c.drawString(p(38), H - p(24), f"Premium Timepieces  |  {Config.WEBSITE_URL.replace('https://','').replace('http://','')}")
        c.drawString(p(38), H - p(30), "New Delhi, India")

        # ── BILL OF SUPPLY label (right) ─────────────────────────────────────
        setfill(cls.GOLD)
        c.setFont("Helvetica-Bold", 14)
        c.drawRightString(W - p(6), H - p(16), "BILL OF SUPPLY")
        setfill(cls.WHITE)
        c.setFont("Helvetica", 8)
        bill_no   = order.get("order_no", "NW-001")
        bill_date = order.get("bill_date") or str(order.get("created_at",""))[:10]
        c.drawRightString(W - p(6), H - p(24), f"Bill No: {bill_no}")
        c.drawRightString(W - p(6), H - p(30), f"Date: {bill_date}")

        # ── Gold divider ─────────────────────────────────────────────────────
        y = H - p(46)
        setstroke(cls.GOLD)
        c.setLineWidth(1.2)
        c.line(p(6), y, W - p(6), y)

        # ── BILL TO block ────────────────────────────────────────────────────
        y -= p(8)
        setfill(cls.GOLD)
        c.setFont("Helvetica-Bold", 8)
        c.drawString(p(6), y, "BILL TO")
        y -= p(6)
        setfill(cls.BLACK)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(p(6), y, order.get("shipping_name") or order.get("customer_name","Customer"))
        y -= p(5)
        c.setFont("Helvetica", 8)
        setfill(cls.DARK)
        addr = order.get("shipping_address","")
        # wrap address at ~60 chars
        words = addr.split(); line_buf = ""; lines = []
        for w in words:
            if len(line_buf) + len(w) + 1 > 60:
                lines.append(line_buf); line_buf = w
            else:
                line_buf = (line_buf + " " + w).strip()
        if line_buf: lines.append(line_buf)
        for ln in lines[:3]:
            c.drawString(p(6), y, ln); y -= p(4.5)
        if order.get("customer_email"):
            c.drawString(p(6), y, order["customer_email"]); y -= p(4.5)
        if order.get("shipping_phone"):
            c.drawString(p(6), y, order["shipping_phone"]); y -= p(4.5)

        # ── Status badge (right side, same row as BILL TO) ──────────────────
        bx = W - p(36); by = H - p(56)
        setfill((0.12, 0.55, 0.22))
        c.roundRect(bx, by, p(30), p(10), p(2), fill=1, stroke=0)
        setfill(cls.WHITE)
        c.setFont("Helvetica-Bold", 9)
        c.drawCentredString(bx + p(15), by + p(3), "✓  PAID")

        # ── Divider ──────────────────────────────────────────────────────────
        y = H - p(100)
        setstroke((0.85, 0.85, 0.85))
        c.setLineWidth(0.5)
        c.line(p(6), y, W - p(6), y)

        # ── Items table ──────────────────────────────────────────────────────
        y -= p(4)
        qty        = int(order.get("quantity", 1))
        prod_name  = order.get("product_name", "Nova Watch")
        pay_method = order.get("payment_method","RAZORPAY").upper()

        if pay_method == "COIN":
            unit_price = f"₪{float(order.get('unit_price_coins', order.get('coins_spent',0))):.2f}"
            svc        = f"₪{float(order.get('service_charge_coins', 0.05)):.2f}"
            total_val  = f"₪{float(order.get('coins_spent',0)):.4f}"
        else:
            unit_price = f"Rs.{int(order.get('unit_price_inr', order.get('inr_paid',0))):,}"
            svc_inr    = int(order.get('service_charge_inr', 0))
            total_inr  = int(order.get('inr_paid', 0))
            svc        = f"Rs.{svc_inr:,}"
            total_val  = f"Rs.{total_inr:,}"

        # Header row
        col_w = [p(12), p(100), p(22), p(32), p(32)]
        hx    = p(6)
        setfill(cls.GOLD)
        c.rect(hx, y - p(7), sum(col_w), p(7), fill=1, stroke=0)
        setfill(cls.BLACK)
        c.setFont("Helvetica-Bold", 8)
        headers = ["#", "Description", "Qty", "Unit Price", "Amount"]
        cx = hx
        for i, (h, w) in enumerate(zip(headers, col_w)):
            c.drawCentredString(cx + w/2, y - p(5.5), h)
            cx += w
        y -= p(7)

        # Item row
        setfill(cls.GREY)
        c.rect(hx, y - p(8), sum(col_w), p(8), fill=1, stroke=0)
        setfill(cls.DARK)
        c.setFont("Helvetica", 8)
        row = ["1", prod_name, str(qty), unit_price, unit_price]
        cx  = hx
        for i, (val, w) in enumerate(zip(row, col_w)):
            c.drawCentredString(cx + w/2, y - p(6), val)
            cx += w
        y -= p(8)

        # ── Totals block ─────────────────────────────────────────────────────
        y -= p(6)
        tx = W - p(80)
        tw = p(74)

        def total_row(label, value, bold=False, gold=False):
            nonlocal y
            if gold:
                setfill(cls.BLACK)
                c.rect(tx, y - p(8), tw, p(8), fill=1, stroke=0)
                setfill(cls.GOLD)
            else:
                setfill(cls.DARK if bold else cls.MUTED)
            font = "Helvetica-Bold" if bold or gold else "Helvetica"
            c.setFont(font, 8 if not gold else 9)
            c.drawString(tx + p(4), y - p(5.5), label)
            c.drawRightString(tx + tw - p(4), y - p(5.5), value)
            y -= p(8)

        total_row("Subtotal:", unit_price)
        total_row("Shipping:", "Rs.0" if pay_method != "COIN" else "₪0")
        total_row("Discount:", "Rs.0" if pay_method != "COIN" else "₪0")
        total_row(f"Service Charge:", svc)
        y -= p(2)
        setstroke(cls.GOLD); c.setLineWidth(0.8)
        c.line(tx, y, tx + tw, y); y -= p(2)
        total_row("TOTAL PAYABLE:", total_val, bold=True, gold=True)

        # ── Payment method box ───────────────────────────────────────────────
        y -= p(10)
        setfill((0.96, 0.96, 0.96))
        c.roundRect(p(6), y - p(20), W - p(12), p(20), p(3), fill=1, stroke=0)
        setfill(cls.DARK)
        c.setFont("Helvetica-Bold", 8)
        c.drawString(p(10), y - p(7), "PAYMENT METHOD")
        c.setFont("Helvetica", 8)
        setfill(cls.MUTED)
        if pay_method == "COIN":
            c.drawString(p(10), y - p(14), f"Nova Coins  |  Coins spent: {float(order.get('coins_spent',0)):.4f}")
        else:
            rz_id = order.get("razorpay_payment_id","")
            c.drawString(p(10), y - p(14), f"Razorpay (UPI/Card)  |  Payment ID: {rz_id}")

        # ── Footer ───────────────────────────────────────────────────────────
        fy = p(14)
        setstroke((0.85,0.85,0.85)); c.setLineWidth(0.5)
        c.line(p(6), fy + p(6), W - p(6), fy + p(6))
        setfill(cls.MUTED)
        c.setFont("Helvetica", 7)
        c.drawCentredString(W/2, fy + p(2),
            f"Thank you for choosing {Config.COMPANY_NAME}  |  {Config.SUPPORT_EMAIL}  |  {Config.WEBSITE_URL.replace('https://','').replace('http://','')}")

        c.showPage()
        c.save()
        return buf.getvalue()

    # ── Order Confirmation email (no PDF — sent immediately on payment) ────────
    @classmethod
    def send_confirmation_email(cls, to_email: str, order: dict) -> None:
        """Send order confirmation email WITHOUT a PDF invoice.
        Used right after the user pays. The invoice PDF is sent separately
        when the admin marks the order as DELIVERED.
        """
        if not Config.SMTP_USER or not Config.SMTP_PASSWORD:
            print(f"[DEV CONFIRM EMAIL] Would send to {to_email}, order {order.get('order_no')}")
            return

        order_no = order.get("order_no", "NW-001")
        html     = EmailTemplates.order_confirmed(order)
        plain    = (
            f"Dear {order.get('shipping_name') or order.get('customer_name','Customer')},\n\n"
            f"Your order has been successfully confirmed and the payment has been received.\n\n"
            f"Order Details:\n"
            f"  - Order/Bill No: {order_no}\n"
            f"  - Product: {order.get('product_name','')}\n"
            f"  - Payment Status: PAID\n\n"
            f"You will receive your invoice once your order is delivered.\n\n"
            f"Thank you for shopping with {Config.COMPANY_NAME}.\n\n"
            f"Best regards,\n{Config.COMPANY_NAME} Team\n"
            f"{Config.SUPPORT_EMAIL} | {Config.WEBSITE_URL}"
        )

        msg = _MIMEMulti("alternative")
        msg["Subject"] = f"Your {Config.COMPANY_NAME} Order Confirmed — {order_no}"
        msg["From"]    = f"{Config.COMPANY_NAME} <{Config.SMTP_USER}>"
        msg["To"]      = to_email
        msg["Reply-To"] = Config.SUPPORT_EMAIL

        msg.attach(_MIMEText(plain, "plain"))
        msg.attach(_MIMEText(html, "html"))

        _smtp_send(msg)

    # ── Invoice email with PDF (sent when admin marks order as DELIVERED) ──────
    @classmethod
    def send_invoice_email(cls, to_email: str, order: dict, pdf_bytes: bytes) -> None:
        """Send invoice email with PDF attached.
        Called only when the admin marks the order status as DELIVERED.
        """
        if not Config.SMTP_USER or not Config.SMTP_PASSWORD:
            print(f"[DEV INVOICE EMAIL] Would send to {to_email}, order {order.get('order_no')}")
            return

        order_no = order.get("order_no", "NW-001")
        html     = EmailTemplates.order_delivered(order)
        plain    = (
            f"Dear {order.get('shipping_name') or order.get('customer_name','Customer')},\n\n"
            f"Great news! Your order has been delivered. Please find your invoice attached.\n\n"
            f"Order Details:\n"
            f"  - Order/Bill No: {order_no}\n"
            f"  - Product: {order.get('product_name','')}\n"
            f"  - Payment Status: PAID\n\n"
            f"We hope you love your purchase! If you have any questions, please reach out to our support team.\n\n"
            f"Best regards,\n{Config.COMPANY_NAME} Team\n"
            f"{Config.SUPPORT_EMAIL} | {Config.WEBSITE_URL}"
        )

        msg = _MIMEMulti("mixed")
        msg["Subject"] = f"Your {Config.COMPANY_NAME} Invoice — {order_no}"
        msg["From"]    = f"{Config.COMPANY_NAME} <{Config.SMTP_USER}>"
        msg["To"]      = to_email
        msg["Reply-To"] = Config.SUPPORT_EMAIL

        alt = _MIMEMulti("alternative")
        alt.attach(_MIMEText(plain, "plain"))
        alt.attach(_MIMEText(html, "html"))
        msg.attach(alt)

        # Attach PDF
        pdf_part = _MIMEApp(pdf_bytes, _subtype="pdf")
        pdf_part.add_header("Content-Disposition", "attachment",
                             filename=f"{Config.COMPANY_NAME.replace(' ','_')}_Invoice_{order_no}.pdf")
        msg.attach(pdf_part)

        _smtp_send(msg)

    # ── Convenience: build PDF + send invoice email in one call ───────────────
    @classmethod
    def dispatch(cls, to_email: str, order: dict) -> None:
        """Generate PDF invoice and email it. Used only on DELIVERED. Errors are logged."""
        try:
            pdf = cls.generate_pdf(order)
            cls.send_invoice_email(to_email, order, pdf)
        except Exception as e:
            print(f"[InvoiceService] Failed to send invoice: {e}")

    # ── Convenience: send confirmation only (no PDF) ──────────────────────────
    @classmethod
    def dispatch_confirmation(cls, to_email: str, order: dict) -> None:
        """Send confirmation-only email (no PDF). Used right after payment. Errors are logged."""
        try:
            cls.send_confirmation_email(to_email, order)
        except Exception as e:
            print(f"[InvoiceService] Failed to send confirmation email: {e}")


# =============================================================================
# GOOGLE AUTH SERVICE
# =============================================================================

class GoogleAuthService:
    """Verifies Google ID tokens."""

    @staticmethod
    def verify_token(credential: str) -> dict:
        if not Config.GOOGLE_CLIENT_ID:
            raise ValueError("GOOGLE_CLIENT_ID is not configured.")
        try:
            from google.oauth2 import id_token as _id_token
            from google.auth.transport import requests as _greq
            return _id_token.verify_oauth2_token(
                credential, _greq.Request(), Config.GOOGLE_CLIENT_ID
            )
        except ImportError:
            pass
        import base64 as _b64, json as _json
        parts = credential.split(".")
        if len(parts) != 3:
            raise ValueError("Invalid JWT")
        padding = 4 - len(parts[1]) % 4
        decoded = _b64.urlsafe_b64decode(parts[1] + "=" * padding)
        payload = _json.loads(decoded)
        if payload.get("aud") != Config.GOOGLE_CLIENT_ID:
            raise ValueError("Token audience mismatch")
        if payload.get("exp", 0) < int(time.time()):
            raise ValueError("Token expired")
        return payload


# =============================================================================
# STORE SERVICE
# =============================================================================

class StoreService:
    """Helpers for the watch store."""

    @staticmethod
    def is_admin(code_name: str) -> bool:
        return (code_name or "").upper() == Config.STORE_ADMIN_CODE_NAME

    @staticmethod
    def slugify(name: str) -> str:
        s = name.lower().strip()
        s = _re.sub(r"[^a-z0-9]+", "-", s)
        return s.strip("-")

    @staticmethod
    def get_setting(key: str, default: str = "") -> str:
        try:
            conn = DatabaseManager.get_connection()
            cur  = conn.cursor()
            cur.execute("SELECT SVAL FROM STORE_SETTINGS WHERE SKEY=%s", (key,))
            row = cur.fetchone()
            cur.close(); conn.close()
            return row[0] if row else default
        except Exception:
            return default


# =============================================================================
# SHARED HELPERS
# =============================================================================

def _generate_coin() -> str:
    return "".join(str(random.randint(1, 90)) for _ in range(10))


def _fetch_coin_row(cur, user_id: str) -> Optional[tuple]:
    cur.execute("SELECT COIN, COINVALUE FROM SZEROS WHERE USER_ID = %s", (user_id,))
    row = cur.fetchone()
    return (row[0], int(row[1])) if row else None


def _fetch_user_by_code_name(cur, code_name: str) -> Optional[tuple]:
    cur.execute(
        "SELECT USER_ID, CODE_NAME FROM NAMES WHERE UPPER(CODE_NAME) = %s", (code_name,)
    )
    row = cur.fetchone()
    return (row[0], row[1]) if row else None


def _require_login() -> tuple:
    """Return (user_id, code_name) or raise PermissionError."""
    if "code_name" not in session or "user_id" not in session:
        raise PermissionError("You are not logged in. Please log in to your Nova account and try again.")
    return session["user_id"], session["code_name"]


def _smtp_send(msg) -> None:
    """Open SMTP, send msg, close safely. Caller must check SMTP credentials first."""
    server = smtplib.SMTP(Config.SMTP_HOST, Config.SMTP_PORT, timeout=15)
    try:
        server.ehlo(); server.starttls(); server.ehlo()
        server.login(Config.SMTP_USER, Config.SMTP_PASSWORD)
        server.send_message(msg)
    finally:
        try: server.quit()
        except Exception: pass


def _send_html_email_async(to_addr: str, subject: str, html: str) -> None:
    """Build a simple HTML MIMEMultipart message and send it in a daemon thread."""
    if not Config.SMTP_USER or not Config.SMTP_PASSWORD:
        logging.info("[DEV EMAIL] Would send to %s: %s", to_addr, subject)
        return

    def _send():
        try:
            msg            = _MIMEMulti("alternative")
            msg["Subject"] = subject
            msg["From"]    = f"{Config.COMPANY_NAME} <{Config.SMTP_USER}>"
            msg["To"]      = to_addr
            msg.attach(_MIMEText(html, "html", "utf-8"))
            _smtp_send(msg)
        except Exception as mail_err:
            logging.warning("Mail send failed to %s: %s", to_addr, mail_err)

    threading.Thread(target=_send, daemon=True).start()


def _admin_guard():
    """Verify the current session user is an admin. Raises PermissionError on failure."""
    user_id, code_name = _require_login()
    if not StoreService.is_admin(code_name):
        raise PermissionError("Access denied. You do not have admin privileges to perform this action.")
    return user_id, code_name


def _admin_error_response(exc: Exception):
    """Map a PermissionError from _admin_guard() to the correct HTTP response."""
    msg = str(exc)
    status = 403 if "admin privileges" in msg else 401
    return jsonify({"error": msg}), status


# =============================================================================
# FLASK APP
# =============================================================================

app = Flask(__name__, static_folder="static")
app.secret_key = Config.SESSION_SECRET

qr_manager = QRManager(Config.FERNET_KEY)

# ── Nova Monitor (SSE live feed) ──────────────────────────────────────────────
from monitor import (
    monitor_bp, monitor,
    emit_register, emit_login, emit_logout, emit_otp,
    emit_coin_send, emit_qr_generated, emit_qr_scanned,
    emit_run, emit_profile, emit_message, emit_connect,
    emit_coin_purchase, emit_coin_refund,
    emit_store_order_coin, emit_store_order_inr, emit_order_status,
    emit_coin_to_cash, emit_contact, emit_broadcast,
    emit_admin, emit_admin_coin_adj,
)
app.register_blueprint(monitor_bp)


_category_fix_done: bool = False
_category_fix_lock       = threading.Lock()


@app.before_request
def _auto_init_db():
    global _category_fix_done
    try:
        # ensure_tables_once() is now called at startup; this is a no-op after that
        DatabaseManager.ensure_tables_once()
        # Fix stale categories — run only ONCE per process
        if not _category_fix_done:
            with _category_fix_lock:
                if not _category_fix_done:
                    try:
                        conn = DatabaseManager.get_connection()
                        cur = conn.cursor()
                        # Reassign products whose CATEGORY slug has no matching STORE_CATEGORIES row
                        # Use first available category (or 'uncategorized' if none exist)
                        cur.execute("SELECT SLUG FROM STORE_CATEGORIES ORDER BY ID ASC LIMIT 1")
                        fallback_row = cur.fetchone()
                        fallback_slug = fallback_row[0] if fallback_row else "uncategorized"
                        cur.execute(
                            """UPDATE STORE_PRODUCTS p
                               SET p.CATEGORY = %s
                               WHERE NOT EXISTS (
                                 SELECT 1 FROM STORE_CATEGORIES c WHERE c.SLUG = p.CATEGORY
                               )""",
                            (fallback_slug,)
                        )
                        conn.commit()
                        cur.close(); conn.close()
                    except Exception:
                        pass
                    finally:
                        _category_fix_done = True
    except Exception as exc:
        app.logger.error("DB init failed: %s", exc)


# ── Static routes ─────────────────────────────────────────────────────────────

@app.get("/")
def home():
    resp = send_file(os.path.join(app.root_path, "index.html"))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"]        = "no-cache"
    return resp


@app.get("/media/<path:filename>")
def media(filename: str):
    return send_from_directory(
        os.path.join(app.root_path, "images"), filename, conditional=True
    )


@app.get("/images/<path:filename>")
def images(filename: str):
    return send_from_directory(os.path.join(app.root_path, "images"), filename)


# ── Image Upload (v6: stored in DB as BLOB) ───────────────────────────────────
ALLOWED_IMG_MIME = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "gif": "image/gif", "webp": "image/webp"
}
MAX_IMG_SIZE_MB = 10

@app.post("/api/admin/upload-image")
def api_upload_image():
    try:
        _admin_guard()
        if "file" not in request.files:
            return jsonify({"error": "No file was attached. Please select an image file and try again."}), 400
        f = request.files["file"]
        if not f or f.filename == "":
            return jsonify({"error": "No file was chosen. Please pick an image before uploading."}), 400
        ext = f.filename.rsplit(".", 1)[-1].lower() if "." in f.filename else ""
        if ext not in ALLOWED_IMG_MIME:
            return jsonify({"error": f"File type '.{ext}' is not supported. Please upload a PNG, JPG, JPEG, GIF, or WEBP image."}), 400
        f.seek(0, 2)
        size_mb = f.tell() / (1024 * 1024)
        f.seek(0)
        if size_mb > MAX_IMG_SIZE_MB:
            return jsonify({"error": f"Your file is {size_mb:.1f} MB, which exceeds the {MAX_IMG_SIZE_MB} MB limit. Please compress or resize the image and try again."}), 400
        img_data  = f.read()
        mime_type = ALLOWED_IMG_MIME[ext]
        unique_name = f"{uuid.uuid4().hex}.{ext}"
        conn = DatabaseManager.get_connection(); cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO PRODUCT_IMAGES (FILENAME, MIME_TYPE, FILE_SIZE, IMAGE_DATA) VALUES (%s, %s, %s, %s)",
                (unique_name, mime_type, len(img_data), img_data)
            )
            conn.commit()
            img_id = cur.lastrowid
        finally:
            cur.close(); conn.close()
        url = f"/api/image/{img_id}/{unique_name}"
        return jsonify({"url": url, "id": img_id, "filename": unique_name})
    except PermissionError:
        return jsonify({"error": "Access denied. You do not have admin privileges to perform this action."}), 403
    except Exception as e:
        return jsonify({"error": "Image upload failed due to a server error. Please try again."}), 500



@app.get("/api/image/<int:img_id>/<path:filename>")
def serve_product_image(img_id: int, filename: str):
    """Serve image directly from MySQL BLOB."""
    conn = DatabaseManager.get_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT IMAGE_DATA, MIME_TYPE FROM PRODUCT_IMAGES WHERE ID=%s", (img_id,))
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "Image not found"}), 404
        img_data, mime_type = row
        resp = Response(bytes(img_data), mimetype=mime_type)
        resp.headers["Cache-Control"] = "public, max-age=31536000"
        return resp
    finally:
        cur.close(); conn.close()


@app.delete("/api/admin/image/<int:img_id>")
def api_delete_image(img_id: int):
    """Delete image from DB."""
    try: _admin_guard()
    except PermissionError as exc: return jsonify({"error": str(exc)}), 403
    conn = DatabaseManager.get_connection(); cur = conn.cursor()
    try:
        cur.execute("DELETE FROM PRODUCT_IMAGES WHERE ID=%s", (img_id,))
        conn.commit()
        return jsonify({"ok": True})
    finally:
        cur.close(); conn.close()


# ── Public config ─────────────────────────────────────────────────────────────

@app.get("/api/config")
def api_config():
    resp = jsonify({
        "google_client_id":  Config.GOOGLE_CLIENT_ID,
        "razorpay_enabled":  bool(Config.RAZORPAY_KEY_ID),
        "support_email":     Config.SUPPORT_EMAIL,
        "website_url":       Config.WEBSITE_URL,
        "company_name":      Config.COMPANY_NAME,
    })
    resp.headers["Cache-Control"] = "public, max-age=300"  # cache 5 min
    return resp


# ── Activity feed ─────────────────────────────────────────────────────────────

@app.get("/api/activity")
def api_activity():
    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(
            """SELECT PAYER_CODE_NAME, RECEIVER_CODE_NAME, CREATED_AT
               FROM TRANSACTIONS ORDER BY CREATED_AT DESC LIMIT 50"""
        )
        rows = cur.fetchall() or []
        return jsonify({
            "ok": True,
            "activity": [
                {"payer_code_name": r[0], "receiver_code_name": r[1], "created_at": str(r[2])}
                for r in rows
            ],
        })
    finally:
        cur.close(); conn.close()


# ── Auth ──────────────────────────────────────────────────────────────────────

@app.post("/api/register")
def api_register():
    payload   = request.get_json(force=True) or {}
    full_name = (payload.get("name")      or "").strip()
    email     = (payload.get("email")     or "").strip().lower()
    phone     = (payload.get("phone")     or "").strip()
    code_name = (payload.get("code_name") or "").strip().upper()
    password  = payload.get("password")  or ""
    google_id = (payload.get("google_id") or "").strip() or None
    user_id   = (payload.get("user_id")   or "").strip().upper()

    if not code_name or not (5 <= len(code_name) <= 7):
        return jsonify({"error": "Code name must be between 5 and 7 characters. Please choose a shorter or longer name."}), 400
    if not password or not (12 <= len(password) <= 16):
        return jsonify({"error": "Password must be between 12 and 16 characters. Please adjust your password and try again."}), 400
    if not user_id:
        user_id = "".join(str(random.randint(0, 9)) for _ in range(8))

    email_verified = 0
    phone_verified = 0
    if email:
        c = DatabaseManager.get_connection(); cx = c.cursor()
        cx.execute(
            "SELECT 1 FROM OTP_STORE WHERE TARGET=%s AND PURPOSE='email' AND USED=1 LIMIT 1",
            (email,),
        )
        if cx.fetchone(): email_verified = 1
        cx.close(); c.close()
    if phone:
        c = DatabaseManager.get_connection(); cx = c.cursor()
        cx.execute(
            "SELECT 1 FROM OTP_STORE WHERE TARGET=%s AND PURPOSE='phone' AND USED=1 LIMIT 1",
            (phone,),
        )
        if cx.fetchone(): phone_verified = 1
        cx.close(); c.close()

    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM NAMES WHERE UPPER(CODE_NAME) = %s", (code_name,))
        if cur.fetchone():
            return jsonify({"error": "This code name is already taken. Please choose a different one."}), 409
        if email:
            cur.execute("SELECT 1 FROM NAMES WHERE EMAIL = %s", (email,))
            if cur.fetchone():
                return jsonify({"error": "This email is already linked to another account. Please log in or use a different email."}), 409

        cur.execute(
            """INSERT INTO NAMES
               (USER_ID, CODE_NAME, PASS_KEY, FULL_NAME, EMAIL, PHONE, GOOGLE_ID,
                EMAIL_VERIFIED, PHONE_VERIFIED)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (user_id, code_name, password, full_name, email, phone,
             google_id, email_verified, phone_verified),
        )

        coin = None
        for _ in range(5):
            candidate = _generate_coin()
            try:
                cur.execute(
                    "INSERT INTO SZEROS (USER_ID, COIN, COINVALUE) VALUES (%s,%s,%s)",
                    (user_id, candidate, 0),
                )
                coin = candidate
                break
            except mysql.connector.Error:
                continue
        if coin is None:
            return jsonify({"error": "Account setup failed due to a coin allocation error. Please try registering again."}), 500

        conn.commit()
        emit_register(full_name, code_name, email)
        return jsonify({"ok": True, "user_id": user_id, "code_name": code_name, "coin": coin})
    except mysql.connector.Error as exc:
        conn.rollback()
        return jsonify({"error": "Registration failed due to a database error. Please try again. If the problem persists, contact support."}), 500
    finally:
        cur.close(); conn.close()


@app.post("/api/login")
def api_login():
    payload   = request.get_json(force=True) or {}
    code_name = (payload.get("code_name") or "").strip().upper()
    password  = payload.get("password") or ""

    if not code_name or not password:
        return jsonify({"error": "Code name and password are both required. Please fill in all fields."}), 400

    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(
            "SELECT USER_ID FROM NAMES WHERE UPPER(CODE_NAME)=%s AND PASS_KEY=%s",
            (code_name, password),
        )
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "Incorrect code name or password. Please double-check and try again."}), 401
        user_id  = row[0]
        coin_row = _fetch_coin_row(cur, user_id)
        if not coin_row:
            return jsonify({"error": "Your wallet could not be found. Please contact support or try logging in again."}), 500
        coin, coinvalue = coin_row
        session["code_name"] = code_name
        session["user_id"]   = user_id
        emit_login(code_name, "email")
        return jsonify({"ok": True, "code_name": code_name, "user_id": user_id,
                        "coin": coin, "coinvalue": coinvalue})
    finally:
        cur.close(); conn.close()


@app.post("/api/logout")
def api_logout():
    emit_logout(session.get("code_name", "?"))
    session.clear()
    return jsonify({"ok": True})


@app.get("/api/me")
def api_me():
    try:
        user_id, code_name = _require_login()
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401
    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        coin_row = _fetch_coin_row(cur, user_id)
        if not coin_row:
            return jsonify({"error": "Your wallet could not be found. Please contact support or try logging in again."}), 500
        coin, coinvalue = coin_row
        return jsonify({"ok": True, "code_name": code_name, "user_id": user_id,
                        "coin": coin, "coinvalue": coinvalue})
    finally:
        cur.close(); conn.close()


@app.get("/api/account/search")
def api_account_search():
    code_name = (request.args.get("code_name") or "").strip().upper()
    if not code_name:
        return jsonify({"error": "Please provide a code name to search for."}), 400
    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        user = _fetch_user_by_code_name(cur, code_name)
        if not user:
            return jsonify({"error": "No account found with that code name. Please check the spelling and try again."}), 404
        user_id, _ = user
        coin_row   = _fetch_coin_row(cur, user_id)
        if not coin_row:
            return jsonify({"error": "Your wallet could not be found. Please contact support or try logging in again."}), 500
        coin, coinvalue = coin_row
        return jsonify({"ok": True, "user_id": user_id, "code_name": code_name,
                        "coin": coin, "coinvalue": coinvalue})
    finally:
        cur.close(); conn.close()


@app.get("/api/transactions")
def api_transactions():
    try:
        user_id, code_name = _require_login()
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401
    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(
            """SELECT RECEIVER_CODE_NAME, PAYER_CODE_NAME, AMOUNT, CREATED_AT
               FROM TRANSACTIONS
               WHERE PAYER_CODE_NAME=%s OR RECEIVER_CODE_NAME=%s
               ORDER BY CREATED_AT DESC LIMIT 40""",
            (code_name, code_name),
        )
        rows = cur.fetchall() or []
        return jsonify({
            "ok": True,
            "transactions": [
                {"receiver_code_name": r[0], "payer_code_name": r[1],
                 "amount": int(r[2]), "created_at": str(r[3])}
                for r in rows
            ],
        })
    finally:
        cur.close(); conn.close()


# ── QR ────────────────────────────────────────────────────────────────────────

@app.get("/api/qr/mine")
def api_qr_mine():
    try:
        user_id, code_name = _require_login()
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401
    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        coin_row = _fetch_coin_row(cur, user_id)
        if not coin_row:
            return jsonify({"error": "Your wallet could not be found. Please contact support or try logging in again."}), 500
        coin, _ = coin_row
        qr_text = qr_manager.generate_payload(code_name=code_name, coin=str(coin))
        # Deep link uses plain code_name only — no TTL, no encrypted payload
        base = Config.APP_BASE_URL or request.url_root.rstrip("/")
        deep_link = f"{base}/?pay={code_name}"
        emit_qr_generated(code_name)
        return jsonify({"ok": True, "qrText": qr_text, "deepLink": deep_link})
    finally:
        cur.close(); conn.close()


@app.get("/api/pay-link/resolve")
def api_pay_link_resolve():
    """Resolve a deep-link pay request by code name — no TTL, returns receiver info."""
    code_name = (request.args.get("code") or "").strip().upper()
    if not code_name:
        return jsonify({"error": "code name required"}), 400
    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        user_row = _fetch_user_by_code_name(cur, code_name)
        if not user_row:
            return jsonify({"error": f"Account '{code_name}' not found."}), 404
        user_id, real_code_name = user_row
        coin_row = _fetch_coin_row(cur, user_id)
        if not coin_row:
            return jsonify({"error": "Receiver wallet not found."}), 500
        coin, _ = coin_row
        return jsonify({
            "ok": True,
            "receiver_code_name": real_code_name,
            "receiver_coin":      str(coin),
        })
    finally:
        cur.close(); conn.close()


@app.post("/api/qr/verify")
def api_qr_verify():
    payload = request.get_json(force=True) or {}
    qr_text = (payload.get("qrText") or "").strip()
    if not qr_text:
        return jsonify({"error": "QR code data is missing. Please scan a valid Nova QR code."}), 400
    data = qr_manager.decrypt_payload(qr_text)
    if not data:
        return jsonify({"error": "This QR code is invalid or has expired (QR codes expire after 60 seconds). Please generate a fresh one and try again."}), 400
    _rcv = str(data["code_name"]).upper()
    emit_qr_scanned(session.get("code_name", "?"), _rcv)
    return jsonify({
        "ok": True,
        "receiver_code_name": _rcv,
        "receiver_coin":      str(data["coin"]),
        "timestamp":          int(data["timestamp"]),
    })


# ── Transfer ──────────────────────────────────────────────────────────────────

def _pay_transfer(conn, payer_code_name, payer_password, amount, receiver_code_name, receiver_coin) -> dict:
    cur = conn.cursor()
    try:
        if amount <= 0:
            raise ValueError("Transfer amount must be greater than zero. Please enter a valid amount.")
        cur.execute(
            "SELECT USER_ID FROM NAMES WHERE UPPER(CODE_NAME)=%s AND PASS_KEY=%s",
            (payer_code_name, payer_password),
        )
        row = cur.fetchone()
        if not row:
            raise PermissionError("Your password is incorrect. Please re-enter your password to authorise the transfer.")
        payer_user_id = row[0]

        cur.execute(
            "SELECT COIN, COINVALUE FROM SZEROS WHERE USER_ID=%s FOR UPDATE", (payer_user_id,)
        )
        prow = cur.fetchone()
        if not prow:
            raise RuntimeError("Your wallet could not be found. Please contact support before trying again.")
        payer_coin_db, payer_balance = prow[0], int(prow[1])
        if payer_balance < amount:
            raise ValueError("You do not have enough coins for this transfer. Please check your balance and try a smaller amount.")

        cur.execute(
            "SELECT USER_ID FROM NAMES WHERE UPPER(CODE_NAME)=%s", (receiver_code_name,)
        )
        rrow = cur.fetchone()
        if not rrow:
            raise LookupError("The receiver's account does not exist. Please verify the code name or QR code and try again.")
        receiver_user_id = rrow[0]

        cur.execute(
            "SELECT COIN, COINVALUE FROM SZEROS WHERE USER_ID=%s FOR UPDATE", (receiver_user_id,)
        )
        rrrow = cur.fetchone()
        if not rrrow:
            raise RuntimeError("The receiver's wallet could not be found. They may need to log in once to activate it.")
        receiver_coin_db, receiver_balance = rrrow[0], int(rrrow[1])
        if str(receiver_coin_db) != str(receiver_coin):
            raise ValueError("The receiver's coin ID does not match. Please scan a fresh QR code and try again.")

        cur.execute("UPDATE SZEROS SET COINVALUE=COINVALUE-%s WHERE USER_ID=%s", (amount, payer_user_id))
        cur.execute("UPDATE SZEROS SET COINVALUE=COINVALUE+%s WHERE USER_ID=%s", (amount, receiver_user_id))
        cur.execute(
            """INSERT INTO TRANSACTIONS
               (PAYER_CODE_NAME,RECEIVER_CODE_NAME,PAYER_COIN,RECEIVER_COIN,AMOUNT)
               VALUES(%s,%s,%s,%s,%s)""",
            (payer_code_name, receiver_code_name, str(payer_coin_db), str(receiver_coin_db), amount),
        )
        conn.commit()
        emit_coin_send(payer_code_name, receiver_code_name, amount)
        return {"ok": True, "payer_balance": payer_balance - amount,
                "receiver_balance": receiver_balance + amount}
    finally:
        cur.close()


@app.post("/api/transaction/pay")
def api_transaction_pay():
    try:
        payer_user_id, payer_code_name = _require_login()
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401

    payload            = request.get_json(force=True) or {}
    amount             = payload.get("amount")
    payer_password     = payload.get("payer_password")
    receiver_code_name = payload.get("receiver_code_name")
    receiver_coin      = payload.get("receiver_coin")
    qr_text            = payload.get("qrText")

    try:
        amount = int(amount)
    except Exception:
        return jsonify({"error": "The amount you entered is not valid. Please enter a whole number (e.g. 5)."}), 400
    if not payer_password:
        return jsonify({"error": "Your password is required to authorise the transfer. Please enter it and try again."}), 400

    if qr_text:
        data = qr_manager.decrypt_payload(str(qr_text).strip())
        if not data:
            return jsonify({"error": "This QR code is invalid or has expired (QR codes expire after 60 seconds). Please generate a fresh one and try again."}), 400
        receiver_code_name = str(data["code_name"]).upper()
        receiver_coin      = str(data["coin"]).strip()

    if not receiver_code_name or not receiver_coin:
        return jsonify({"error": "Receiver details are missing. Please scan a QR code or enter the receiver's code name and coin."}), 400

    conn = DatabaseManager.get_connection()
    try:
        result = _pay_transfer(
            conn, payer_code_name, str(payer_password), amount,
            str(receiver_code_name).strip().upper(), str(receiver_coin).strip(),
        )
        return jsonify(result)
    except (PermissionError, ValueError, LookupError) as exc:
        conn.rollback()
        return jsonify({"error": str(exc)}), 400
    except Exception as exc:
        conn.rollback()
        return jsonify({"error": "The transfer could not be completed due to a server error. No coins were moved. Please try again."}), 500
    finally:
        conn.close()


# ── Run / Earn ────────────────────────────────────────────────────────────────

@app.post("/api/run/earn")
def api_run_earn():
    try:
        user_id, code_name = _require_login()
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401

    payload = request.get_json(force=True) or {}
    points  = payload.get("gps_points")
    if not points or not isinstance(points, list):
        return jsonify({"error": "No GPS data was received. Please make sure location access is enabled and try starting your run again."}), 400
    if len(points) > 10000:
        return jsonify({"error": "Your run session has too many GPS points (max 10 000). Please end and re-start the run."}), 400

    valid_km, err = RunValidator.validate(points)
    if err:
        return jsonify({"error": err}), 400

    if valid_km < MIN_SESSION_KM:
        return jsonify({
            "ok": True, "coins_earned": 0, "distance_km": round(valid_km, 3),
            "message": (
                f"You ran {valid_km:.2f} km. "
                f"Need {MIN_SESSION_KM - valid_km:.2f} more km for 1 coin. "
                "Distance does NOT carry forward."
            ),
        }), 200

    coins_to_award = int(valid_km / MIN_SESSION_KM) * COINS_PER_10KM
    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(
            """SELECT COALESCE(SUM(COINS_EARNED),0) FROM RUN_SESSIONS
               WHERE USER_ID=%s AND DATE(CREATED_AT)=CURDATE()""",
            (user_id,),
        )
        row         = cur.fetchone()
        coins_today = int(row[0]) if row else 0
        if coins_today >= MAX_COINS_PER_DAY:
            return jsonify({"error": "You have already earned your coin for today. The next session opens before 5:00 AM IST. See you tomorrow!"}), 429

        coins_to_award = min(coins_to_award, MAX_COINS_PER_DAY - coins_today)
        cur.execute(
            "INSERT INTO RUN_SESSIONS (USER_ID,DISTANCE_KM,COINS_EARNED) VALUES(%s,%s,%s)",
            (user_id, round(valid_km, 3), coins_to_award),
        )
        cur.execute(
            "UPDATE SZEROS SET COINVALUE=COINVALUE+%s WHERE USER_ID=%s",
            (coins_to_award, user_id),
        )
        conn.commit()
        coin_row    = _fetch_coin_row(cur, user_id)
        new_balance = coin_row[1] if coin_row else 0
        emit_run(code_name, valid_km, coins_to_award)
        return jsonify({
            "ok": True, "coins_earned": coins_to_award,
            "distance_km": round(valid_km, 3), "new_balance": new_balance,
            "message": f"Congratulations! Earned {coins_to_award} coin for {valid_km:.2f} km.",
        })
    except Exception as exc:
        conn.rollback()
        return jsonify({"error": "Your run was validated but coins could not be awarded due to a server error. Please contact support with today's date and time."}), 500
    finally:
        cur.close(); conn.close()


# ── OTP ───────────────────────────────────────────────────────────────────────

@app.post("/api/otp/send-email")
def api_otp_send_email():
    payload = request.get_json(force=True) or {}
    email   = (payload.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return jsonify({"error": "Please enter a valid email address (e.g. you@example.com)."}), 400
    if not OTPService._check_rate_limit(email, "email"):
        return jsonify({"error": "Too many verification codes have been sent to this email. You can request a maximum of 4 per hour. Please wait before trying again."}), 429
    otp = OTPService.generate()
    OTPService.store(email, otp, "email")

    def _send():
        try:
            OTPService.send_email(email, otp)
        except Exception as exc:
            app.logger.error("Email OTP send error: %s", exc)

    threading.Thread(target=_send, daemon=True).start()
    emit_otp(email, "email")
    return jsonify({"ok": True, "message": "OTP sent to email"})


@app.post("/api/otp/verify-email")
def api_otp_verify_email():
    payload = request.get_json(force=True) or {}
    email   = (payload.get("email") or "").strip().lower()
    otp     = (payload.get("otp")   or "").strip()
    if not email or not otp:
        return jsonify({"error": "Both your email address and the verification code are required. Please fill in both fields."}), 400
    if not OTPService.verify(email, otp, "email"):
        return jsonify({"error": "The verification code is incorrect or has expired (codes are valid for 5 minutes). Please request a new one."}), 400
    return jsonify({"ok": True})


# ── Google login ──────────────────────────────────────────────────────────────

@app.post("/api/google-login")
def api_google_login():
    payload    = request.get_json(force=True) or {}
    credential = (payload.get("credential") or "").strip()
    if not credential:
        return jsonify({"error": "Google sign-in data is missing. Please try clicking the Google button again."}), 400
    try:
        idinfo = GoogleAuthService.verify_token(credential)
    except Exception as exc:
        return jsonify({"error": "Google sign-in failed. Please try signing in again. If the issue continues, try a different browser."}), 401

    google_id = idinfo.get("sub", "")
    email     = (idinfo.get("email") or "").lower()
    name      = idinfo.get("name", "")
    if not google_id or not email:
        return jsonify({"error": "Your Google account did not share enough information (email or ID missing). Please allow all permissions and try again."}), 400

    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(
            "SELECT USER_ID, CODE_NAME FROM NAMES "
            "WHERE GOOGLE_ID=%s OR (EMAIL=%s AND EMAIL_VERIFIED=1) LIMIT 1",
            (google_id, email),
        )
        row = cur.fetchone()
        if row:
            user_id, code_name = row
            cur.execute(
                "UPDATE NAMES SET GOOGLE_ID=%s WHERE USER_ID=%s AND (GOOGLE_ID IS NULL OR GOOGLE_ID='')",
                (google_id, user_id),
            )
            conn.commit()
            session["code_name"] = code_name
            session["user_id"]   = user_id
            coin_row = _fetch_coin_row(cur, user_id)
            coin, coinvalue = coin_row if coin_row else ("", 0)
            emit_login(code_name, "google")
            return jsonify({"ok": True, "code_name": code_name, "user_id": user_id,
                            "coin": coin, "coinvalue": coinvalue})
        else:
            return jsonify({"ok": True, "needs_setup": True,
                            "email": email, "name": name, "google_id": google_id})
    finally:
        cur.close(); conn.close()


# ── Razorpay coin purchase ────────────────────────────────────────────────────

@app.post("/api/payment/create-order")
def api_payment_create_order():
    try:
        user_id, code_name = _require_login()
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401

    payload = request.get_json(force=True) or {}
    coins   = int(payload.get("coins",  0))
    amount  = int(payload.get("amount", 0))
    if coins <= 0 or amount <= 0:
        return jsonify({"error": "The number of coins or the amount entered is invalid. Please enter values greater than zero."}), 400

    receipt = f"nova_{user_id}_{int(time.time())}"
    try:
        order = PaymentService.create_order(amount, receipt)
    except Exception as exc:
        app.logger.error("Razorpay create order error: %s", exc)
        return jsonify({"error": "Could not connect to the payment gateway. Please check your internet connection and try again."}), 502

    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO COIN_PURCHASES
               (USER_ID,CODE_NAME,RAZORPAY_ORDER_ID,COINS,AMOUNT_PAISE,STATUS)
               VALUES(%s,%s,%s,%s,%s,'PENDING')""",
            (user_id, code_name, order["id"], coins, amount),
        )
        conn.commit()
    finally:
        cur.close(); conn.close()

    return jsonify({"ok": True, "razorpay_order_id": order["id"],
                    "amount": order["amount"], "currency": order["currency"]})


@app.post("/api/payment/verify")
def api_payment_verify():
    try:
        user_id, code_name = _require_login()
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401

    payload    = request.get_json(force=True) or {}
    order_id   = (payload.get("razorpay_order_id")   or "").strip()
    payment_id = (payload.get("razorpay_payment_id") or "").strip()
    signature  = (payload.get("razorpay_signature")  or "").strip()
    coins      = int(payload.get("coins",  0))
    amount     = int(payload.get("amount", 0))

    if not order_id or not payment_id or not signature:
        return jsonify({"error": "Payment information is incomplete. Please do not close the window during payment and try again."}), 400
    if not PaymentService.verify_signature(order_id, payment_id, signature):
        return jsonify({"error": "Payment could not be verified. Please contact support with your order details if money was deducted."}), 400

    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(
            "SELECT ID, STATUS FROM COIN_PURCHASES WHERE RAZORPAY_ORDER_ID=%s FOR UPDATE",
            (order_id,),
        )
        row = cur.fetchone()
        if row and row[1] == "SUCCESS":
            return jsonify({"error": "This order has already been completed. Please check your order history."}), 409

        invoice_no = f"NOVA-{int(time.time())}-{uuid.uuid4().hex[:6].upper()}"
        cur.execute("UPDATE SZEROS SET COINVALUE=COINVALUE+%s WHERE USER_ID=%s", (coins, user_id))
        cur.execute(
            """UPDATE COIN_PURCHASES
               SET STATUS='SUCCESS', RAZORPAY_PAYMENT_ID=%s, INVOICE_NO=%s
               WHERE RAZORPAY_ORDER_ID=%s AND USER_ID=%s""",
            (payment_id, invoice_no, order_id, user_id),
        )
        if cur.rowcount == 0:
            cur.execute(
                """INSERT INTO COIN_PURCHASES
                   (USER_ID,CODE_NAME,RAZORPAY_ORDER_ID,RAZORPAY_PAYMENT_ID,
                    COINS,AMOUNT_PAISE,STATUS,INVOICE_NO)
                   VALUES(%s,%s,%s,%s,%s,%s,'SUCCESS',%s)""",
                (user_id, code_name, order_id, payment_id, coins, amount, invoice_no),
            )
        conn.commit()
        coin_row    = _fetch_coin_row(cur, user_id)
        new_balance = coin_row[1] if coin_row else 0

        # ── Send invoice email for coin purchase (async, non-blocking) ────
        try:
            cur.execute(
                "SELECT FULL_NAME, EMAIL FROM NAMES WHERE USER_ID=%s LIMIT 1",
                (user_id,)
            )
            urow = cur.fetchone()
            if urow and urow[1]:
                amount_inr  = amount // 100          # paise → rupees
                invoice_order = {
                    "order_no":             invoice_no,
                    "product_name":         f"{coins} Nova Coin{'s' if coins != 1 else ''}",
                    "quantity":             coins,
                    "unit_price_inr":       amount_inr // coins if coins else amount_inr,
                    "service_charge_inr":   0,
                    "inr_paid":             amount_inr,
                    "razorpay_payment_id":  payment_id,
                    "payment_method":       "RAZORPAY",
                    "customer_name":        urow[0] or code_name,
                    "customer_email":       urow[1],
                    "shipping_name":        "",
                    "shipping_address":     "",
                    "shipping_phone":       "",
                    "created_at":           datetime.now().strftime("%d %B %Y"),
                    "bill_date":            datetime.now().strftime("%d %B %Y"),
                }
                threading.Thread(
                    target=InvoiceService.dispatch,
                    args=(urow[1], invoice_order),
                    daemon=True
                ).start()
        except Exception as _ie:
            print(f"[Invoice] Could not send coin-buy invoice: {_ie}")

        emit_coin_purchase(code_name, coins, amount // 100, invoice_no)
        return jsonify({"ok": True, "coins_added": coins,
                        "new_balance": new_balance, "invoice_no": invoice_no})
    except Exception as exc:
        conn.rollback()
        return jsonify({"error": "Payment was received but coins could not be credited. Please contact support with your payment ID and we will resolve it promptly."}), 500
    finally:
        cur.close(); conn.close()


# =============================================================================
# STORE
# =============================================================================

@app.get("/api/store/products")
def api_store_products():
    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(
            """SELECT ID,NAME,SLUG,PRICE_COINS,PRICE_INR,DESCRIPTION,
                      FEATURES,IMAGE_URL,CATEGORY,STOCK,ACTIVE,
                      COALESCE(COINS_ENABLED,1),COALESCE(INR_ENABLED,1)
               FROM STORE_PRODUCTS WHERE ACTIVE=1 ORDER BY ID DESC"""
        )
        rows = cur.fetchall() or []
        products = [
            {"id": r[0], "name": r[1], "slug": r[2], "price_coins": float(r[3]),
             "price_inr": int(r[4]), "description": r[5] or "", "features": r[6] or "",
             "image_url": r[7] or "", "category": r[8] or "",
             "stock": int(r[9]), "active": bool(r[10]),
             "coins_enabled": bool(r[11]), "inr_enabled": bool(r[12])}
            for r in rows
        ]
        settings = {
            k: StoreService.get_setting(k)
            for k in ["coin_to_inr", "service_charge_inr", "service_charge_coins",
                      "store_name", "razorpay_enabled", "coins_enabled"]
        }
        return jsonify({"ok": True, "products": products, "settings": settings})
    finally:
        cur.close(); conn.close()


@app.post("/api/store/order/coin")
def api_store_order_coin():
    try:
        user_id, code_name = _require_login()
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401

    p          = request.get_json(force=True) or {}
    product_id = int(p.get("product_id", 0))
    password   = p.get("password", "")
    shipping   = p.get("shipping", {})
    if not product_id or not password:
        return jsonify({"error": "Product selection and your password are both required to complete the purchase."}), 400

    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        cur.execute("SELECT ID,NAME,PRICE_COINS,STOCK FROM STORE_PRODUCTS WHERE ID=%s AND ACTIVE=1", (product_id,))
        prod = cur.fetchone()
        if not prod:
            return jsonify({"error": "This product is no longer available. Please go back and choose another item."}), 404
        _, prod_name, price_coins, stock = prod
        price_coins = float(price_coins)
        if stock <= 0:
            return jsonify({"error": "This product is currently out of stock. Please check back later or choose a different item."}), 400

        cur.execute("SELECT USER_ID FROM NAMES WHERE USER_ID=%s AND PASS_KEY=%s", (user_id, password))
        if not cur.fetchone():
            return jsonify({"error": "The password you entered is wrong. Please try again."}), 401

        cur.execute("SELECT COIN, COINVALUE FROM SZEROS WHERE USER_ID=%s FOR UPDATE", (user_id,))
        crow = cur.fetchone()
        if not crow:
            return jsonify({"error": "Your wallet could not be found. Please contact support or try logging in again."}), 500
        balance = float(crow[1])

        svc   = float(StoreService.get_setting("service_charge_coins", "0.05"))
        total = price_coins + svc
        if balance < total:
            return jsonify({"error": f"You do not have enough coins. This purchase requires {total:.4f} coins but your balance is {balance:.0f}. Earn more coins by running or purchase them from the store."}), 400

        new_bal = int(balance - total)
        cur.execute("UPDATE SZEROS SET COINVALUE=%s WHERE USER_ID=%s", (new_bal, user_id))
        cur.execute("UPDATE STORE_PRODUCTS SET STOCK=STOCK-1 WHERE ID=%s", (product_id,))

        order_no = f"NW-{int(time.time())}-{uuid.uuid4().hex[:6].upper()}"
        cur.execute(
            """INSERT INTO STORE_ORDERS
               (ORDER_NO,USER_ID,CODE_NAME,PRODUCT_ID,PRODUCT_NAME,PAYMENT_METHOD,
                COINS_SPENT,STATUS,SHIPPING_NAME,SHIPPING_ADDRESS,SHIPPING_PHONE)
               VALUES(%s,%s,%s,%s,%s,'COIN',%s,'CONFIRMED',%s,%s,%s)""",
            (order_no, user_id, code_name, product_id, prod_name, total,
             shipping.get("name",""), shipping.get("address",""), shipping.get("phone","")),
        )
        conn.commit()

        # ── Send invoice email for coin purchases (async, non-blocking) ───
        try:
            cur.execute(
                "SELECT n.EMAIL, n.FULL_NAME FROM NAMES n WHERE n.USER_ID=%s LIMIT 1",
                (user_id,)
            )
            irow = cur.fetchone()
            if irow and irow[0]:
                svc_coins    = float(StoreService.get_setting("service_charge_coins", "0.05"))
                invoice_order = {
                    "order_no":               order_no,
                    "product_name":           prod_name,
                    "quantity":               1,
                    "unit_price_coins":       price_coins,
                    "service_charge_coins":   svc_coins,
                    "coins_spent":            total,
                    "payment_method":         "COIN",
                    "customer_name":          irow[1] or code_name,
                    "customer_email":         irow[0],
                    "shipping_name":          shipping.get("name", ""),
                    "shipping_address":       shipping.get("address", ""),
                    "shipping_phone":         shipping.get("phone", ""),
                    "created_at":             datetime.now().strftime("%d %B %Y"),
                    "bill_date":              datetime.now().strftime("%d %B %Y"),
                }
                threading.Thread(
                    target=InvoiceService.dispatch_confirmation,
                    args=(irow[0], invoice_order),
                    daemon=True
                ).start()
        except Exception as _ie:
            print(f"[Invoice] Could not send coin-purchase invoice: {_ie}")

        emit_store_order_coin(code_name, prod_name, total, order_no)
        return jsonify({"ok": True, "order_no": order_no, "coins_spent": total, "new_balance": new_bal})
    except Exception as exc:
        conn.rollback(); return jsonify({"error": "Your order could not be saved due to a server error. No coins were deducted. Please try again."}), 500
    finally:
        cur.close(); conn.close()


@app.post("/api/store/order/razorpay/create")
def api_store_rz_create():
    try:
        user_id, code_name = _require_login()
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401

    p          = request.get_json(force=True) or {}
    product_id = int(p.get("product_id", 0))
    if not product_id:
        return jsonify({"error": "No product was selected. Please choose a product before proceeding."}), 400

    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        cur.execute("SELECT ID,NAME,PRICE_INR,STOCK FROM STORE_PRODUCTS WHERE ID=%s AND ACTIVE=1", (product_id,))
        prod = cur.fetchone()
        if not prod:
            return jsonify({"error": "Product not found"}), 404
        _, prod_name, price_inr, stock = prod
        if stock <= 0:
            return jsonify({"error": "Out of stock"}), 400

        svc         = int(StoreService.get_setting("service_charge_inr", "500"))
        total_paise = (int(price_inr) + svc) * 100
        receipt     = f"nw_{user_id}_{product_id}_{int(time.time())}"
        order       = PaymentService.create_order(total_paise, receipt)

        order_no = f"NW-{int(time.time())}-{uuid.uuid4().hex[:6].upper()}"
        cur.execute(
            """INSERT INTO STORE_ORDERS
               (ORDER_NO,USER_ID,CODE_NAME,PRODUCT_ID,PRODUCT_NAME,PAYMENT_METHOD,
                INR_PAID,RAZORPAY_ORDER_ID,STATUS,SHIPPING_NAME,SHIPPING_ADDRESS,SHIPPING_PHONE)
               VALUES(%s,%s,%s,%s,%s,'RAZORPAY',%s,%s,'PENDING','','','')""",
            (order_no, user_id, code_name, product_id, prod_name, total_paise, order["id"]),
        )
        conn.commit()
        return jsonify({"ok": True, "razorpay_order_id": order["id"], "amount": order["amount"],
                        "currency": order["currency"], "order_no": order_no, "product_name": prod_name})
    except Exception as exc:
        conn.rollback()
        err_msg = str(exc)
        # Show actual Razorpay error to help debug
        if "Razorpay API error" in err_msg or "allowlist" in err_msg.lower():
            return jsonify({"error": f"Payment gateway error: {err_msg}"}), 500
        return jsonify({"error": "Your order could not be created due to a server error. Please try again or contact support."}), 500
    finally:
        cur.close(); conn.close()


@app.post("/api/store/order/razorpay/verify")
def api_store_rz_verify():
    try:
        user_id, code_name = _require_login()
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401

    p        = request.get_json(force=True) or {}
    rz_oid   = (p.get("razorpay_order_id")  or "").strip()
    rz_pid   = (p.get("razorpay_payment_id") or "").strip()
    rz_sig   = (p.get("razorpay_signature")  or "").strip()
    order_no = (p.get("order_no")            or "").strip()
    shipping = p.get("shipping", {})

    if not all([rz_oid, rz_pid, rz_sig, order_no]):
        return jsonify({"error": "Missing payment details"}), 400
    if not PaymentService.verify_signature(rz_oid, rz_pid, rz_sig):
        return jsonify({"error": "Payment verification failed. Please contact support if money was deducted from your account."}), 400

    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    _rz_product = "?"
    _rz_inr     = 0
    try:
        cur.execute(
            "SELECT ID,PRODUCT_ID,STATUS FROM STORE_ORDERS WHERE ORDER_NO=%s AND USER_ID=%s FOR UPDATE",
            (order_no, user_id),
        )
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "Order not found. Please check your order history or contact support."}), 404
        if row[2] == "CONFIRMED":
            return jsonify({"error": "This order has already been confirmed. No further action is needed."}), 409

        cur.execute("UPDATE STORE_PRODUCTS SET STOCK=STOCK-1 WHERE ID=%s AND STOCK>0", (row[1],))
        cur.execute(
            """UPDATE STORE_ORDERS
               SET STATUS='CONFIRMED', RAZORPAY_PAYMENT_ID=%s,
                   SHIPPING_NAME=%s, SHIPPING_ADDRESS=%s, SHIPPING_PHONE=%s
               WHERE ORDER_NO=%s""",
            (rz_pid, shipping.get("name",""), shipping.get("address",""),
             shipping.get("phone",""), order_no),
        )
        conn.commit()

        # ── Send invoice email (async, non-blocking) ──────────────────────
        try:
            cur.execute(
                "SELECT n.EMAIL, n.FULL_NAME, o.PRODUCT_NAME, o.INR_PAID FROM NAMES n "
                "JOIN STORE_ORDERS o ON n.USER_ID=o.USER_ID "
                "WHERE o.ORDER_NO=%s LIMIT 1", (order_no,)
            )
            irow = cur.fetchone()
            if irow and irow[0]:
                svc_inr      = int(StoreService.get_setting("service_charge_inr", "500"))
                total_paise  = int(irow[3] or 0)
                total_inr    = total_paise // 100          # paise → rupees
                base_inr     = max(0, total_inr - svc_inr)
                _rz_product  = irow[2] or "Nova Watch"
                _rz_inr      = total_inr
                invoice_order = {
                    "order_no":             order_no,
                    "product_name":         irow[2] or "Nova Watch",
                    "quantity":             1,
                    "unit_price_inr":       base_inr,
                    "service_charge_inr":   svc_inr,
                    "inr_paid":             total_inr,
                    "razorpay_payment_id":  rz_pid,
                    "payment_method":       "RAZORPAY",
                    "customer_name":        irow[1] or code_name,
                    "customer_email":       irow[0],
                    "shipping_name":        shipping.get("name",""),
                    "shipping_address":     shipping.get("address",""),
                    "shipping_phone":       shipping.get("phone",""),
                    "created_at":           datetime.now().strftime("%d %B %Y"),
                    "bill_date":            datetime.now().strftime("%d %B %Y"),
                }
                threading.Thread(
                    target=InvoiceService.dispatch_confirmation,
                    args=(irow[0], invoice_order),
                    daemon=True
                ).start()
        except Exception as _ie:
            print(f"[Invoice] Could not send invoice: {_ie}")

        emit_store_order_inr(code_name, _rz_product, _rz_inr, order_no)
        return jsonify({"ok": True, "order_no": order_no})
    except Exception as exc:
        conn.rollback(); return jsonify({"error": "Order confirmation failed. Please contact support with your payment ID if money was charged."}), 500
    finally:
        cur.close(); conn.close()


@app.get("/api/store/invoice/<order_no>")
def api_store_invoice_pdf(order_no):
    """Download Bill-of-Supply PDF for a confirmed store order."""
    try:
        user_id, code_name = _require_login()
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401

    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(
            """SELECT o.ORDER_NO, o.PRODUCT_NAME, o.PAYMENT_METHOD,
                      o.COINS_SPENT, o.INR_PAID, o.RAZORPAY_PAYMENT_ID,
                      o.SHIPPING_NAME, o.SHIPPING_ADDRESS, o.SHIPPING_PHONE,
                      o.CREATED_AT, o.STATUS,
                      n.FULL_NAME, n.EMAIL
               FROM STORE_ORDERS o
               JOIN NAMES n ON n.USER_ID = o.USER_ID
               WHERE o.ORDER_NO=%s AND o.USER_ID=%s LIMIT 1""",
            (order_no, user_id),
        )
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "Order not found."}), 404
        if row[10] != "DELIVERED":
            return jsonify({"error": "Invoice is only available once your order has been delivered."}), 400

        pay_method = (row[2] or "").upper()
        svc_inr    = int(StoreService.get_setting("service_charge_inr", "500"))
        svc_coins  = float(StoreService.get_setting("service_charge_coins", "0.05"))
        total_inr  = int(row[4] or 0) // 100  # paise -> rupees
        coins_spent= float(row[3] or 0)

        invoice_order = {
            "order_no":             row[0],
            "product_name":         row[1] or "Nova Watch",
            "quantity":             1,
            "payment_method":       pay_method,
            "unit_price_inr":       max(0, total_inr - svc_inr) if pay_method == "RAZORPAY" else 0,
            "service_charge_inr":   svc_inr,
            "inr_paid":             total_inr,
            "unit_price_coins":     max(0.0, coins_spent - svc_coins) if pay_method == "COIN" else 0,
            "service_charge_coins": svc_coins,
            "coins_spent":          coins_spent,
            "razorpay_payment_id":  row[5] or "",
            "customer_name":        row[11] or code_name,
            "customer_email":       row[12] or "",
            "shipping_name":        row[6] or "",
            "shipping_address":     row[7] or "",
            "shipping_phone":       row[8] or "",
            "created_at":           str(row[9])[:10] if row[9] else "",
            "bill_date":            str(row[9])[:10] if row[9] else "",
        }

        pdf_bytes = InvoiceService.generate_pdf(invoice_order)
        filename  = f"Nova_Invoice_{order_no}.pdf"
        response  = Response(pdf_bytes, mimetype="application/pdf")
        response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
    except Exception as exc:
        print(f"[InvoicePDF] Error: {exc}")
        return jsonify({"error": "Could not generate invoice PDF. Please try again."}), 500
    finally:
        cur.close(); conn.close()


@app.get("/api/store/my-orders")
def api_store_my_orders():
    try:
        user_id, _ = _require_login()
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401
    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(
            """SELECT ORDER_NO, PRODUCT_NAME, PAYMENT_METHOD, COINS_SPENT, INR_PAID, STATUS,
                      CREATED_AT,
                      COALESCE(REFUND_COINS, 0),
                      REFUNDED_AT
               FROM STORE_ORDERS WHERE USER_ID=%s ORDER BY CREATED_AT DESC LIMIT 30""",
            (user_id,),
        )
        rows = cur.fetchall() or []
        return jsonify({
            "ok": True,
            "orders": [
                {"order_no":       r[0], "product_name":  r[1], "payment_method": r[2],
                 "coins_spent":    float(r[3] or 0), "inr_paid": int(r[4] or 0),
                 "status":         r[5], "created_at":   str(r[6]),
                 "refund_coins":   float(r[7] or 0),
                 "refunded_at":    str(r[8]) if r[8] else None}
                for r in rows
            ],
        })
    finally:
        cur.close(); conn.close()


@app.delete("/api/account/delete")
def api_account_delete():
    try:
        user_id, _ = _require_login()
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401

    p        = request.get_json(force=True) or {}
    password = p.get("password", "")
    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        cur.execute("SELECT USER_ID FROM NAMES WHERE USER_ID=%s AND PASS_KEY=%s", (user_id, password))
        if not cur.fetchone():
            return jsonify({"error": "Incorrect password"}), 401
        cur.execute("SELECT COINVALUE FROM SZEROS WHERE USER_ID=%s", (user_id,))
        crow = cur.fetchone()
        if crow and int(crow[0]) > 0:
            return jsonify({"error": f"Account cannot be deleted while you still have {crow[0]} coins. Please transfer all coins to another account first, then try again."}), 400
        cur.execute("DELETE FROM NAMES WHERE USER_ID=%s", (user_id,))
        conn.commit()
        session.clear()
        return jsonify({"ok": True})
    except Exception as exc:
        conn.rollback(); return jsonify({"error": "Account deletion failed due to a server error. Please try again or contact support."}), 500
    finally:
        cur.close(); conn.close()


# ── Admin ─────────────────────────────────────────────────────────────────────

@app.get("/api/admin/products")
def api_admin_products():
    try: _admin_guard()
    except PermissionError as exc: return _admin_error_response(exc)
    conn = DatabaseManager.get_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT ID,NAME,SLUG,PRICE_COINS,PRICE_INR,DESCRIPTION,FEATURES,IMAGE_URL,CATEGORY,STOCK,ACTIVE,COALESCE(COINS_ENABLED,1),COALESCE(INR_ENABLED,1) FROM STORE_PRODUCTS ORDER BY ID DESC")
        rows = cur.fetchall() or []
        return jsonify({"ok": True, "products": [
            {"id":r[0],"name":r[1],"slug":r[2],"price_coins":float(r[3]),"price_inr":int(r[4]),
             "description":r[5] or "","features":r[6] or "","image_url":r[7] or "",
             "category":r[8] or "","stock":int(r[9]),"active":bool(r[10]),
             "coins_enabled":bool(r[11]),"inr_enabled":bool(r[12])} for r in rows]})
    finally: cur.close(); conn.close()


@app.post("/api/admin/product")
def api_admin_add_product():
    try: _, _adm = _admin_guard()
    except PermissionError as exc: return _admin_error_response(exc)
    p = request.get_json(force=True) or {}
    name = (p.get("name") or "").strip()
    if not name: return jsonify({"error": "Product name is required. Please enter a name before saving."}), 400
    slug = StoreService.slugify(name)
    conn = DatabaseManager.get_connection(); cur = conn.cursor()
    try:
        base, i = slug, 1
        while True:
            cur.execute("SELECT ID FROM STORE_PRODUCTS WHERE SLUG=%s", (slug,))
            if not cur.fetchone(): break
            slug = f"{base}-{i}"; i += 1
        cur.execute(
            """INSERT INTO STORE_PRODUCTS (NAME,SLUG,PRICE_COINS,PRICE_INR,DESCRIPTION,FEATURES,IMAGE_URL,CATEGORY,STOCK,ACTIVE,COINS_ENABLED,INR_ENABLED)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (name, slug, float(p.get("price_coins",1.0)), int(p.get("price_inr",10000)),
             (p.get("description") or "").strip(), (p.get("features") or "").strip(),
             (p.get("image_url") or "").strip(), (p.get("category") or "").strip().lower() or "uncategorized",
             int(p.get("stock",10)), int(p.get("active",1)),
             int(p.get("coins_enabled", 1)), int(p.get("inr_enabled", 1))),
        )
        conn.commit()
        emit_admin(_adm, "ADD_PRODUCT", name)
        return jsonify({"ok": True, "slug": slug, "id": cur.lastrowid})
    except Exception as exc:
        conn.rollback(); return jsonify({"error": "Product could not be saved. Please check all fields and try again."}), 500
    finally: cur.close(); conn.close()


@app.put("/api/admin/product/<int:product_id>")
def api_admin_edit_product(product_id: int):
    try: _, _adm = _admin_guard()
    except PermissionError as exc: return _admin_error_response(exc)
    p = request.get_json(force=True) or {}
    fields, vals = [], []
    for field, col in [("name","NAME"),("price_coins","PRICE_COINS"),("price_inr","PRICE_INR"),
                       ("description","DESCRIPTION"),("features","FEATURES"),("image_url","IMAGE_URL"),
                       ("category","CATEGORY"),("stock","STOCK"),("active","ACTIVE"),
                       ("coins_enabled","COINS_ENABLED"),("inr_enabled","INR_ENABLED")]:
        if field in p:
            val = p[field]
            # No category override — admin is free to set any slug
            fields.append(f"{col}=%s"); vals.append(val)
    if not fields: return jsonify({"error": "No changes were provided. Please update at least one field before saving."}), 400
    vals.append(product_id)
    conn = DatabaseManager.get_connection(); cur = conn.cursor()
    try:
        cur.execute(f"UPDATE STORE_PRODUCTS SET {', '.join(fields)} WHERE ID=%s", vals)
        conn.commit()
        emit_admin(_adm, "EDIT_PRODUCT", str(product_id))
        return jsonify({"ok": True})
    except Exception as exc:
        conn.rollback(); return jsonify({"error": "Product update failed. Please check all fields and try again."}), 500
    finally: cur.close(); conn.close()


@app.delete("/api/admin/product/<int:product_id>")
def api_admin_delete_product(product_id: int):
    try: _, _adm = _admin_guard()
    except PermissionError as exc: return _admin_error_response(exc)
    conn = DatabaseManager.get_connection(); cur = conn.cursor()
    try:
        # Get image_url to also delete from PRODUCT_IMAGES if it was a DB-stored image
        cur.execute("SELECT IMAGE_URL FROM STORE_PRODUCTS WHERE ID=%s", (product_id,))
        row = cur.fetchone()
        image_url = (row[0] or "").strip() if row else ""

        cur.execute("DELETE FROM STORE_PRODUCTS WHERE ID=%s", (product_id,))
        conn.commit()

        if image_url.startswith("/api/image/"):
            try:
                parts = image_url.split("/")
                img_id = int(parts[3])
                cur2 = conn.cursor()
                cur2.execute("DELETE FROM PRODUCT_IMAGES WHERE ID=%s", (img_id,))
                conn.commit()
                cur2.close()
            except Exception:
                pass

        emit_admin(_adm, "DELETE_PRODUCT", str(product_id))
        return jsonify({"ok": True})
    except Exception as exc:
        conn.rollback(); return jsonify({"error": "Product could not be deleted. It may be linked to existing orders."}), 500
    finally: cur.close(); conn.close()


@app.get("/api/admin/orders")
def api_admin_orders():
    try: _admin_guard()
    except PermissionError as exc: return _admin_error_response(exc)
    conn = DatabaseManager.get_connection(); cur = conn.cursor()
    try:
        cur.execute(
            """SELECT ORDER_NO,CODE_NAME,PRODUCT_NAME,PAYMENT_METHOD,COINS_SPENT,INR_PAID,
                      STATUS,SHIPPING_NAME,SHIPPING_ADDRESS,SHIPPING_PHONE,CREATED_AT
               FROM STORE_ORDERS ORDER BY CREATED_AT DESC LIMIT 300"""
        )
        rows = cur.fetchall() or []
        return jsonify({"ok": True, "orders": [
            {"order_no":r[0],"code_name":r[1],"product_name":r[2],"payment_method":r[3],
             "coins_spent":float(r[4] or 0),"inr_paid":int(r[5] or 0),"status":r[6],
             "shipping_name":r[7],"shipping_address":r[8],"shipping_phone":r[9],
             "created_at":str(r[10])} for r in rows]})
    finally: cur.close(); conn.close()


@app.put("/api/admin/order/<order_no>")
def api_admin_update_order(order_no: str):
    try: _admin_guard()
    except PermissionError as exc: return _admin_error_response(exc)
    p      = request.get_json(force=True) or {}
    status = (p.get("status") or "").strip().upper()
    if status not in {"PENDING","CONFIRMED","SHIPPED","DELIVERED","CANCELLED"}:
        return jsonify({"error": "The status you selected is not valid. Allowed values are: PENDING, CONFIRMED, SHIPPED, DELIVERED, CANCELLED."}), 400
    conn = DatabaseManager.get_connection(); cur = conn.cursor()
    try:
        cur.execute(
            "SELECT USER_ID, CODE_NAME, PAYMENT_METHOD, COINS_SPENT, INR_PAID, STATUS FROM STORE_ORDERS WHERE ORDER_NO=%s",
            (order_no,)
        )
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "Order not found."}), 404
        user_id, code_name, pay_method, coins_spent, inr_paid, old_status = row

        # LOCK: if order is already CANCELLED (and refund done), block any further status changes
        if (old_status or "").upper() == "CANCELLED":
            return jsonify({"error": "This order has been cancelled and the refund is complete. Status cannot be changed again."}), 400

        cur.execute("UPDATE STORE_ORDERS SET STATUS=%s WHERE ORDER_NO=%s", (status, order_no))

        refunded      = False
        refund_coins  = 0.0

        if status == "CANCELLED":
            if pay_method == "COIN" and float(coins_spent or 0) > 0:
                refund_coins = float(coins_spent)
                cur.execute("SELECT COINVALUE FROM SZEROS WHERE USER_ID=%s FOR UPDATE", (user_id,))
                wallet = cur.fetchone()
                if wallet:
                    cur.execute(
                        "UPDATE SZEROS SET COINVALUE = COINVALUE + %s WHERE USER_ID=%s",
                        (refund_coins, user_id)
                    )
                    # Record refund on the order row
                    cur.execute(
                        "UPDATE STORE_ORDERS SET REFUND_COINS=%s, REFUNDED_AT=NOW() WHERE ORDER_NO=%s",
                        (refund_coins, order_no)
                    )
                    # Log the refund transaction
                    cur.execute(
                        """INSERT INTO TRANSACTIONS
                           (PAYER_CODE_NAME, RECEIVER_CODE_NAME, PAYER_COIN, RECEIVER_COIN, AMOUNT)
                           SELECT 'ADMIN_REFUND', n.CODE_NAME, 'REFUND', s.COIN, %s
                           FROM NAMES n JOIN SZEROS s ON n.USER_ID=s.USER_ID WHERE n.USER_ID=%s""",
                        (refund_coins, user_id)
                    )
                    refunded = True
            elif pay_method in ("INR", "RAZORPAY") and int(inr_paid or 0) > 0:
                cur.execute(
                    "INSERT INTO ADMIN_COIN_LOG (ADMIN_CODE_NAME, TARGET_CODE_NAME, AMOUNT, NOTE) VALUES (%s,%s,%s,%s)",
                    ("SYSTEM", code_name, 0,
                     f"INR order {order_no} cancelled — manual Razorpay refund of ₹{int(inr_paid or 0)//100} required")
                )
                refunded = True

        conn.commit()

        # ── Send invoice PDF email when order is marked DELIVERED ─────────
        if status == "DELIVERED":
            try:
                cur.execute(
                    """SELECT n.EMAIL, n.FULL_NAME, o.PRODUCT_NAME, o.INR_PAID,
                              o.COINS_SPENT, o.PAYMENT_METHOD, o.RAZORPAY_PAYMENT_ID,
                              o.SHIPPING_NAME, o.SHIPPING_ADDRESS, o.SHIPPING_PHONE,
                              o.CREATED_AT
                       FROM NAMES n
                       JOIN STORE_ORDERS o ON n.USER_ID = o.USER_ID
                       WHERE o.ORDER_NO=%s LIMIT 1""",
                    (order_no,)
                )
                irow = cur.fetchone()
                if irow and irow[0]:
                    pay_m       = (irow[5] or "RAZORPAY").upper()
                    svc_inr     = int(StoreService.get_setting("service_charge_inr", "500"))
                    svc_coins   = float(StoreService.get_setting("service_charge_coins", "0.05"))
                    total_paise = int(irow[3] or 0)
                    total_inr   = total_paise // 100
                    coins_sp    = float(irow[4] or 0)
                    invoice_order = {
                        "order_no":             order_no,
                        "product_name":         irow[2] or "Nova Watch",
                        "quantity":             1,
                        "payment_method":       pay_m,
                        "unit_price_inr":       max(0, total_inr - svc_inr) if pay_m == "RAZORPAY" else 0,
                        "service_charge_inr":   svc_inr,
                        "inr_paid":             total_inr,
                        "unit_price_coins":     max(0.0, coins_sp - svc_coins) if pay_m == "COIN" else 0,
                        "service_charge_coins": svc_coins,
                        "coins_spent":          coins_sp,
                        "razorpay_payment_id":  irow[6] or "",
                        "customer_name":        irow[1] or code_name,
                        "customer_email":       irow[0],
                        "shipping_name":        irow[7] or "",
                        "shipping_address":     irow[8] or "",
                        "shipping_phone":       irow[9] or "",
                        "created_at":           str(irow[10])[:10] if irow[10] else "",
                        "bill_date":            datetime.now().strftime("%d %B %Y"),
                    }
                    threading.Thread(
                        target=InvoiceService.dispatch,
                        args=(irow[0], invoice_order),
                        daemon=True
                    ).start()
            except Exception as _ie:
                print(f"[Invoice] Could not send delivery invoice for {order_no}: {_ie}")

        emit_order_status(order_no, code_name, status)
        if refunded and refund_coins > 0:
            emit_coin_refund(code_name, refund_coins)
        return jsonify({"ok": True, "refunded": refunded, "refund_coins": refund_coins})
    except Exception as exc:
        conn.rollback()
        return jsonify({"error": str(exc) if "cancelled" in str(exc) else "Order status could not be updated. Please try again."}), (400 if "cancelled" in str(exc) else 500)
    finally: cur.close(); conn.close()


@app.get("/api/admin/users")
def api_admin_users():
    try: _admin_guard()
    except PermissionError as exc: return _admin_error_response(exc)
    conn = DatabaseManager.get_connection(); cur = conn.cursor()
    try:
        cur.execute(
            """SELECT n.USER_ID,n.CODE_NAME,n.FULL_NAME,n.EMAIL,n.PHONE,n.CREATED_AT,
                      COALESCE(s.COINVALUE,0) AS BAL
               FROM NAMES n LEFT JOIN SZEROS s ON n.USER_ID=s.USER_ID ORDER BY n.CREATED_AT DESC"""
        )
        rows = cur.fetchall() or []
        return jsonify({"ok": True, "users": [
            {"user_id":r[0],"code_name":r[1],"full_name":r[2],"email":r[3],"phone":r[4],
             "created_at":str(r[5]),"balance":float(r[6])} for r in rows]})
    finally: cur.close(); conn.close()


@app.get("/api/admin/settings")
def api_admin_get_settings():
    try: _admin_guard()
    except PermissionError as exc: return _admin_error_response(exc)
    conn = DatabaseManager.get_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT SKEY,SVAL FROM STORE_SETTINGS")
        rows = cur.fetchall() or []
        return jsonify({"ok": True, "settings": {r[0]: r[1] for r in rows}})
    finally: cur.close(); conn.close()


@app.post("/api/admin/settings")
def api_admin_save_settings():
    try: _admin_guard()
    except PermissionError as exc: return _admin_error_response(exc)
    p    = request.get_json(force=True) or {}
    conn = DatabaseManager.get_connection(); cur = conn.cursor()
    try:
        for k, v in p.items():
            cur.execute(
                "INSERT INTO STORE_SETTINGS (SKEY,SVAL) VALUES(%s,%s) ON DUPLICATE KEY UPDATE SVAL=%s",
                (k, str(v), str(v)),
            )
        conn.commit(); return jsonify({"ok": True})
    except Exception as exc:
        conn.rollback(); return jsonify({"error": "Settings could not be saved due to a server error. Please try again."}), 500
    finally: cur.close(); conn.close()


# =============================================================================
# ADMIN — CATEGORY MANAGEMENT
# =============================================================================

@app.get("/api/admin/categories")
def api_admin_get_categories():
    try: _admin_guard()
    except PermissionError as exc: return _admin_error_response(exc)
    conn = DatabaseManager.get_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT ID, NAME, SLUG, CREATED_AT FROM STORE_CATEGORIES ORDER BY ID ASC")
        rows = cur.fetchall() or []
        return jsonify({"ok": True, "categories": [
            {"id": r[0], "name": r[1], "slug": r[2], "created_at": str(r[3])} for r in rows]})
    finally: cur.close(); conn.close()


@app.post("/api/admin/category")
def api_admin_add_category():
    try: _admin_guard()
    except PermissionError as exc: return _admin_error_response(exc)
    p = request.get_json(force=True) or {}
    name = (p.get("name") or "").strip()
    if not name: return jsonify({"error": "Category name is required."}), 400
    slug = StoreService.slugify(name)
    conn = DatabaseManager.get_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT ID FROM STORE_CATEGORIES WHERE SLUG=%s", (slug,))
        if cur.fetchone():
            return jsonify({"error": "A category with this name already exists."}), 400
        cur.execute("INSERT INTO STORE_CATEGORIES (NAME, SLUG) VALUES (%s, %s)", (name, slug))
        conn.commit()
        return jsonify({"ok": True, "id": cur.lastrowid, "slug": slug})
    except Exception as exc:
        conn.rollback(); return jsonify({"error": "Category could not be created."}), 500
    finally: cur.close(); conn.close()


@app.delete("/api/admin/category/<int:cat_id>")
def api_admin_delete_category(cat_id: int):
    try: _admin_guard()
    except PermissionError as exc: return _admin_error_response(exc)
    conn = DatabaseManager.get_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT SLUG FROM STORE_CATEGORIES WHERE ID=%s", (cat_id,))
        row = cur.fetchone()
        if not row: return jsonify({"error": "Category not found."}), 404
        slug = row[0]
        # Find first available category that is NOT the one being deleted
        cur.execute("SELECT SLUG FROM STORE_CATEGORIES WHERE ID != %s ORDER BY ID ASC LIMIT 1", (cat_id,))
        fallback_row = cur.fetchone()
        fallback_slug = fallback_row[0] if fallback_row else None
        # Reassign products to fallback category (or mark as uncategorized if no other category exists)
        if fallback_slug:
            cur.execute("UPDATE STORE_PRODUCTS SET CATEGORY=%s WHERE CATEGORY=%s", (fallback_slug, slug))
        else:
            cur.execute("UPDATE STORE_PRODUCTS SET CATEGORY='uncategorized' WHERE CATEGORY=%s", (slug,))
        cur.execute("DELETE FROM STORE_CATEGORIES WHERE ID=%s", (cat_id,))
        conn.commit()
        return jsonify({"ok": True, "products_reassigned_to": fallback_slug or "uncategorized"})
    except Exception as exc:
        conn.rollback(); return jsonify({"error": "Category could not be deleted."}), 500
    finally: cur.close(); conn.close()


# =============================================================================
# ADMIN — USER COIN MANAGEMENT
# =============================================================================

@app.post("/api/admin/user/coins")
def api_admin_adjust_coins():
    try:
        _, admin_code = _admin_guard()
    except PermissionError as exc: return _admin_error_response(exc)
    p = request.get_json(force=True) or {}
    target_code = (p.get("code_name") or "").strip().upper()
    amount      = float(p.get("amount") or 0)
    note        = (p.get("note") or "Admin adjustment").strip()[:200]
    if not target_code: return jsonify({"error": "Target user code name is required."}), 400
    if amount == 0:     return jsonify({"error": "Amount cannot be zero."}), 400
    conn = DatabaseManager.get_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT USER_ID, CODE_NAME FROM NAMES WHERE CODE_NAME=%s", (target_code,))
        row = cur.fetchone()
        if not row: return jsonify({"error": "User not found."}), 404
        user_id, code_name = row
        cur.execute("SELECT COINVALUE FROM SZEROS WHERE USER_ID=%s FOR UPDATE", (user_id,))
        wallet = cur.fetchone()
        if not wallet:
            return jsonify({"error": "User wallet not found."}), 404
        new_balance = float(wallet[0]) + amount
        if new_balance < 0:
            return jsonify({"error": f"Insufficient balance. Current: {float(wallet[0]):.4f} coins."}), 400
        cur.execute("UPDATE SZEROS SET COINVALUE = COINVALUE + %s WHERE USER_ID=%s", (amount, user_id))
        cur.execute(
            "INSERT INTO ADMIN_COIN_LOG (ADMIN_CODE_NAME, TARGET_CODE_NAME, AMOUNT, NOTE) VALUES (%s,%s,%s,%s)",
            (admin_code, code_name, amount, note)
        )
        conn.commit()
        emit_admin_coin_adj(admin_code, code_name, amount, note)
        return jsonify({"ok": True, "new_balance": new_balance})
    except Exception as exc:
        conn.rollback(); return jsonify({"error": "Coin adjustment failed."}), 500
    finally: cur.close(); conn.close()


@app.get("/api/admin/user/coin-logs")
def api_admin_coin_logs():
    try: _admin_guard()
    except PermissionError as exc: return _admin_error_response(exc)
    conn = DatabaseManager.get_connection(); cur = conn.cursor()
    try:
        cur.execute(
            "SELECT ID, ADMIN_CODE_NAME, TARGET_CODE_NAME, AMOUNT, NOTE, CREATED_AT FROM ADMIN_COIN_LOG ORDER BY CREATED_AT DESC LIMIT 200"
        )
        rows = cur.fetchall() or []
        return jsonify({"ok": True, "logs": [
            {"id":r[0],"admin":r[1],"target":r[2],"amount":float(r[3]),"note":r[4],"created_at":str(r[5])}
            for r in rows]})
    finally: cur.close(); conn.close()


# =============================================================================
# ADMIN — REMOVE USER PERMANENTLY
# =============================================================================

@app.delete("/api/admin/user/<code_name>")
def api_admin_delete_user(code_name: str):
    try:
        _, admin_cn = _admin_guard()
    except PermissionError as exc: return _admin_error_response(exc)
    code_name = (code_name or "").strip().upper()
    if code_name == admin_cn:
        return jsonify({"error": "You cannot delete your own admin account."}), 400
    conn = DatabaseManager.get_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT USER_ID FROM NAMES WHERE CODE_NAME=%s", (code_name,))
        row = cur.fetchone()
        if not row: return jsonify({"error": "User not found."}), 404
        user_id = row[0]
        # Cascade deletes handle child tables via FK ON DELETE CASCADE
        cur.execute("DELETE FROM NAMES WHERE USER_ID=%s", (user_id,))
        conn.commit()
        emit_admin(admin_cn, "DELETE_USER", code_name)
        return jsonify({"ok": True, "message": f"User {code_name} permanently removed."})
    except Exception as exc:
        conn.rollback(); return jsonify({"error": "User could not be deleted."}), 500
    finally: cur.close(); conn.close()


# =============================================================================
# ADMIN — DROP ALL TABLES
# =============================================================================

@app.delete("/api/admin/database/drop-all")
def api_admin_drop_all_tables():
    try: _, _adm = _admin_guard()
    except PermissionError as exc: return _admin_error_response(exc)
    p = request.get_json(force=True) or {}
    confirm = (p.get("confirm") or "").strip()
    if confirm != "DROP ALL TABLES":
        return jsonify({"error": "Please type 'DROP ALL TABLES' exactly to confirm this destructive action."}), 400
    conn = DatabaseManager.get_connection(); cur = conn.cursor()
    try:
        cur.execute("SET FOREIGN_KEY_CHECKS=0")
        cur.execute("SHOW TABLES")
        tables = [r[0] for r in (cur.fetchall() or [])]
        for t in tables:
            cur.execute(f"DROP TABLE IF EXISTS `{t}`")
        cur.execute("SET FOREIGN_KEY_CHECKS=1")
        conn.commit()
        # Reset init flag so tables are recreated on next request
        DatabaseManager._init_done = False
        emit_admin(_adm, "DROP_ALL_TABLES", "DANGER")
        return jsonify({"ok": True, "tables_dropped": tables})
    except Exception as exc:
        conn.rollback(); return jsonify({"error": "Failed to drop tables."}), 500
    finally: cur.close(); conn.close()


# =============================================================================
# ADMIN — PUBLIC CATEGORIES (for store front)
# =============================================================================

@app.get("/api/categories")
def api_get_categories():
    conn = DatabaseManager.get_connection(); cur = conn.cursor()
    try:
        cur.execute("SELECT ID, NAME, SLUG FROM STORE_CATEGORIES ORDER BY ID ASC")
        rows = cur.fetchall() or []
        return jsonify({"ok": True, "categories": [
            {"id": r[0], "name": r[1], "slug": r[2]} for r in rows]})
    finally: cur.close(); conn.close()

@app.get("/api/profile/me")
def api_profile_me():
    try:
        user_id, code_name = _require_login()
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401
    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(
            "SELECT BIO,WORK_TITLE,WORK_COMPANY,WORK_LOCATION,IS_PUBLIC,PHOTO_URL FROM USER_PROFILES WHERE USER_ID=%s",
            (user_id,)
        )
        row = cur.fetchone()
        if row:
            bio, title, company, location, is_public, photo_url = row
        else:
            bio = title = company = location = photo_url = ""
            is_public = 1
        # pending connection requests count
        cur.execute(
            "SELECT COUNT(*) FROM CONNECTIONS WHERE RECEIVER_ID=%s AND STATUS='PENDING'",
            (user_id,)
        )
        pending = cur.fetchone()[0] or 0
        return jsonify({"ok": True, "code_name": code_name, "bio": bio or "",
                        "work_title": title or "", "work_company": company or "",
                        "work_location": location or "", "is_public": bool(is_public),
                        "photo_url": photo_url or "",
                        "pending_connections": int(pending)})
    finally:
        cur.close(); conn.close()


@app.post("/api/profile/save")
def api_profile_save():
    try:
        user_id, code_name = _require_login()
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401
    p = request.get_json(force=True) or {}
    bio       = (p.get("bio") or "")[:500]
    title     = (p.get("work_title") or "")[:120]
    company   = (p.get("work_company") or "")[:120]
    location  = (p.get("work_location") or "")[:120]
    photo_url = (p.get("photo_url") or "")[:500]
    is_public = 1 if p.get("is_public", True) else 0
    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(
            """INSERT INTO USER_PROFILES (USER_ID,BIO,WORK_TITLE,WORK_COMPANY,WORK_LOCATION,PHOTO_URL,IS_PUBLIC)
               VALUES (%s,%s,%s,%s,%s,%s,%s)
               ON DUPLICATE KEY UPDATE BIO=%s,WORK_TITLE=%s,WORK_COMPANY=%s,WORK_LOCATION=%s,PHOTO_URL=%s,IS_PUBLIC=%s""",
            (user_id, bio, title, company, location, photo_url, is_public,
             bio, title, company, location, photo_url, is_public)
        )
        conn.commit()
        emit_profile(code_name)
        return jsonify({"ok": True})
    except Exception as exc:
        conn.rollback()
        return jsonify({"error": "Profile could not be saved."}), 500
    finally:
        cur.close(); conn.close()


@app.get("/api/profiles/public")
def api_profiles_public():
    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(
            """SELECT n.CODE_NAME, p.BIO, p.WORK_TITLE, p.WORK_COMPANY, p.WORK_LOCATION, p.PHOTO_URL
               FROM NAMES n
               INNER JOIN USER_PROFILES p ON n.USER_ID = p.USER_ID
               WHERE p.IS_PUBLIC = 1
               ORDER BY p.UPDATED_AT DESC
               LIMIT 50"""
        )
        rows = cur.fetchall() or []
        return jsonify({"ok": True, "profiles": [
            {"code_name": r[0], "bio": r[1] or "", "work_title": r[2] or "",
             "work_company": r[3] or "", "work_location": r[4] or "", "photo_url": r[5] or ""}
            for r in rows
        ]})
    finally:
        cur.close(); conn.close()


@app.get("/api/profile/<code_name>")
def api_profile_view(code_name: str):
    code_name = (code_name or "").strip().upper()
    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        user = _fetch_user_by_code_name(cur, code_name)
        if not user:
            return jsonify({"error": "Profile not found."}), 404
        target_id, _ = user
        cur.execute(
            "SELECT BIO,WORK_TITLE,WORK_COMPANY,WORK_LOCATION,IS_PUBLIC,PHOTO_URL FROM USER_PROFILES WHERE USER_ID=%s",
            (target_id,)
        )
        row = cur.fetchone()
        if not row or not row[4]:
            return jsonify({"error": "This profile is private."}), 403
        bio, title, company, location, _, photo_url = row
        return jsonify({"ok": True, "code_name": code_name, "bio": bio or "",
                        "work_title": title or "", "work_company": company or "",
                        "work_location": location or "", "photo_url": photo_url or ""})
    finally:
        cur.close(); conn.close()


# =============================================================================
# CONNECTION ROUTES
# =============================================================================

@app.post("/api/connect/request")
def api_connect_request():
    try:
        user_id, code_name = _require_login()
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401
    p = request.get_json(force=True) or {}
    target_code = (p.get("code_name") or "").strip().upper()
    if not target_code or target_code == code_name:
        return jsonify({"error": "Invalid target."}), 400
    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        target = _fetch_user_by_code_name(cur, target_code)
        if not target:
            return jsonify({"error": "User not found."}), 404
        target_id, _ = target
        # Check if already connected or pending
        cur.execute(
            "SELECT STATUS FROM CONNECTIONS WHERE (REQUESTER_ID=%s AND RECEIVER_ID=%s) OR (REQUESTER_ID=%s AND RECEIVER_ID=%s)",
            (user_id, target_id, target_id, user_id)
        )
        existing = cur.fetchone()
        if existing:
            return jsonify({"error": f"Connection already {existing[0].lower()}."}), 400
        cur.execute(
            "INSERT INTO CONNECTIONS (REQUESTER_ID, RECEIVER_ID, STATUS) VALUES (%s,%s,'PENDING')",
            (user_id, target_id)
        )
        conn.commit()
        emit_connect(code_name, target_code, "REQUESTED")
        return jsonify({"ok": True, "message": "Connection request sent."})
    except Exception:
        conn.rollback()
        return jsonify({"error": "Could not send request."}), 500
    finally:
        cur.close(); conn.close()


@app.post("/api/connect/respond")
def api_connect_respond():
    try:
        user_id, code_name = _require_login()
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401
    p = request.get_json(force=True) or {}
    conn_id = p.get("id")
    action  = (p.get("action") or "").upper()
    if action not in {"ACCEPT", "DECLINE"}:
        return jsonify({"error": "Invalid action."}), 400
    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(
            "SELECT ID FROM CONNECTIONS WHERE ID=%s AND RECEIVER_ID=%s AND STATUS='PENDING'",
            (conn_id, user_id)
        )
        if not cur.fetchone():
            return jsonify({"error": "Request not found."}), 404
        if action == "ACCEPT":
            cur.execute("UPDATE CONNECTIONS SET STATUS='ACCEPTED' WHERE ID=%s", (conn_id,))
        else:
            cur.execute("DELETE FROM CONNECTIONS WHERE ID=%s", (conn_id,))
        conn.commit()
        new_status = "ACCEPTED" if action == "ACCEPT" else "DECLINED"
        emit_connect("?", code_name, new_status)
        return jsonify({"ok": True})
    except Exception:
        conn.rollback()
        return jsonify({"error": "Could not update request."}), 500
    finally:
        cur.close(); conn.close()


@app.get("/api/connections")
def api_connections_list():
    try:
        user_id, code_name = _require_login()
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401
    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        # Accepted connections (with photo_url)
        cur.execute(
            """SELECT c.ID,
                      CASE WHEN c.REQUESTER_ID=%s THEN nr.CODE_NAME ELSE nq.CODE_NAME END AS OTHER_NAME,
                      CASE WHEN c.REQUESTER_ID=%s THEN c.RECEIVER_ID ELSE c.REQUESTER_ID END AS OTHER_ID,
                      COALESCE(
                        CASE WHEN c.REQUESTER_ID=%s THEN pr.PHOTO_URL ELSE pq.PHOTO_URL END,
                        ''
                      ) AS PHOTO_URL
               FROM CONNECTIONS c
               JOIN NAMES nq ON c.REQUESTER_ID = nq.USER_ID
               JOIN NAMES nr ON c.RECEIVER_ID  = nr.USER_ID
               LEFT JOIN USER_PROFILES pq ON c.REQUESTER_ID = pq.USER_ID
               LEFT JOIN USER_PROFILES pr ON c.RECEIVER_ID  = pr.USER_ID
               WHERE (c.REQUESTER_ID=%s OR c.RECEIVER_ID=%s) AND c.STATUS='ACCEPTED'""",
            (user_id, user_id, user_id, user_id, user_id)
        )
        accepted = [{"id": r[0], "code_name": r[1], "user_id": r[2], "photo_url": r[3] or ""} for r in (cur.fetchall() or [])]
        # Pending incoming (with photo_url)
        cur.execute(
            """SELECT c.ID, n.CODE_NAME, c.REQUESTER_ID, COALESCE(p.PHOTO_URL, '') AS PHOTO_URL
               FROM CONNECTIONS c
               JOIN NAMES n ON c.REQUESTER_ID = n.USER_ID
               LEFT JOIN USER_PROFILES p ON c.REQUESTER_ID = p.USER_ID
               WHERE c.RECEIVER_ID=%s AND c.STATUS='PENDING'""",
            (user_id,)
        )
        pending = [{"id": r[0], "code_name": r[1], "user_id": r[2], "photo_url": r[3] or ""} for r in (cur.fetchall() or [])]
        # Sent requests (with photo_url)
        cur.execute(
            """SELECT c.ID, n.CODE_NAME, c.RECEIVER_ID, COALESCE(p.PHOTO_URL, '') AS PHOTO_URL
               FROM CONNECTIONS c
               JOIN NAMES n ON c.RECEIVER_ID = n.USER_ID
               LEFT JOIN USER_PROFILES p ON c.RECEIVER_ID = p.USER_ID
               WHERE c.REQUESTER_ID=%s AND c.STATUS='PENDING'""",
            (user_id,)
        )
        sent = [{"id": r[0], "code_name": r[1], "user_id": r[2], "photo_url": r[3] or ""} for r in (cur.fetchall() or [])]
        return jsonify({"ok": True, "accepted": accepted, "pending": pending, "sent": sent})
    finally:
        cur.close(); conn.close()


@app.delete("/api/connect/<int:conn_id>")
def api_connect_remove(conn_id: int):
    try:
        user_id, _ = _require_login()
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401
    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(
            "DELETE FROM CONNECTIONS WHERE ID=%s AND (REQUESTER_ID=%s OR RECEIVER_ID=%s)",
            (conn_id, user_id, user_id)
        )
        conn.commit()
        return jsonify({"ok": True})
    except Exception:
        conn.rollback()
        return jsonify({"error": "Could not remove connection."}), 500
    finally:
        cur.close(); conn.close()


# =============================================================================
# EPHEMERAL MESSAGING (in-memory only — never stored)
# =============================================================================

# { room_key: [{"id": str, "from": str, "text_enc": str, "ts": float}, ...] }
_msg_rooms: dict = {}
_msg_lock  = threading.Lock()
_MSG_TTL   = 50  # seconds

def _room_key(uid_a: str, uid_b: str) -> str:
    return ":".join(sorted([uid_a, uid_b]))

def _purge_old_messages() -> None:
    now = time.time()
    with _msg_lock:
        for key in list(_msg_rooms.keys()):
            _msg_rooms[key] = [m for m in _msg_rooms[key] if now - m["ts"] < _MSG_TTL]
            if not _msg_rooms[key]:
                del _msg_rooms[key]

@app.post("/api/msg/send")
def api_msg_send():
    try:
        user_id, code_name = _require_login()
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401
    p = request.get_json(force=True) or {}
    peer_id   = (p.get("peer_id") or "").strip()
    text_enc  = (p.get("text") or "").strip()
    if not peer_id or not text_enc:
        return jsonify({"error": "Missing fields."}), 400
    if len(text_enc) > 4096:
        return jsonify({"error": "Message too long."}), 400
    # Verify they are connected
    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(
            """SELECT ID FROM CONNECTIONS
               WHERE ((REQUESTER_ID=%s AND RECEIVER_ID=%s) OR (REQUESTER_ID=%s AND RECEIVER_ID=%s))
               AND STATUS='ACCEPTED'""",
            (user_id, peer_id, peer_id, user_id)
        )
        if not cur.fetchone():
            return jsonify({"error": "Not connected with this user."}), 403
    finally:
        cur.close(); conn.close()
    _purge_old_messages()
    key = _room_key(user_id, peer_id)
    now = time.time()
    with _msg_lock:
        # Server-side dedup: reject if identical text from same sender within 2 seconds
        recent = _msg_rooms.get(key, [])
        for prev in reversed(recent):
            if now - prev["ts"] > 2:
                break
            if prev["from_id"] == user_id and prev["text"] == text_enc:
                # Duplicate detected — return the existing msg_id silently
                return jsonify({"ok": True, "msg_id": prev["id"], "dedup": True})
        msg = {"id": str(uuid.uuid4())[:8], "from": code_name,
               "from_id": user_id, "text": text_enc, "ts": now}
        _msg_rooms.setdefault(key, []).append(msg)
    emit_message(code_name, f"uid:{peer_id}")
    return jsonify({"ok": True, "msg_id": msg["id"]})


@app.get("/api/msg/poll")
def api_msg_poll():
    try:
        user_id, code_name = _require_login()
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401
    peer_id = (request.args.get("peer_id") or "").strip()
    since   = float(request.args.get("since") or 0)
    if not peer_id:
        return jsonify({"error": "Missing peer_id."}), 400
    _purge_old_messages()
    key = _room_key(user_id, peer_id)
    now = time.time()
    with _msg_lock:
        room = _msg_rooms.get(key, [])
        # Fast path: no new messages — return minimal response (reduces JSON payload)
        if not room or (room and room[-1]["ts"] <= since):
            return jsonify({"ok": True, "messages": [], "server_ts": now})
        msgs = [
            {"id": m["id"], "from": m["from"], "text": m["text"],
             "ts": m["ts"], "mine": m["from_id"] == user_id,
             "expires_in": max(0, _MSG_TTL - (now - m["ts"]))}
            for m in room
            if m["ts"] > since
        ]
    return jsonify({"ok": True, "messages": msgs, "server_ts": now})


# =============================================================================
# COIN → CASH CONVERSION REQUEST  (sends email to SMTP_USER / admin)
# =============================================================================

@app.post("/api/coin-to-cash-request")
def api_coin_to_cash_request():
    """
    User agrees to 10% deduction and submits a cash-out request.
    Sends a formatted email to SMTP_USER (admin) with full details.
    Also sends a confirmation email to the user.
    """
    try:
        user_id, code_name = _require_login()
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 401

    data       = request.get_json(silent=True) or {}
    coins_req  = data.get("coins")            # float / int
    bank_name  = (data.get("bank_name") or "").strip()
    account_no = (data.get("account_no") or "").strip()
    ifsc       = (data.get("ifsc") or "").strip()
    acc_holder = (data.get("account_holder") or "").strip()
    upi_id     = (data.get("upi_id") or "").strip()
    agreed     = data.get("agreed_10pct", False)

    if not agreed:
        return jsonify({"error": "You must agree to the 10% conversion charge."}), 400
    if not coins_req or float(coins_req) <= 0:
        return jsonify({"error": "Enter a valid number of coins to convert."}), 400
    if not (bank_name or upi_id):
        return jsonify({"error": "Provide bank details or UPI ID."}), 400
    if bank_name and not (account_no and ifsc and acc_holder):
        return jsonify({"error": "For bank transfer, provide account number, IFSC, and account holder name."}), 400

    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        coin_row = _fetch_coin_row(cur, user_id)
        if not coin_row:
            return jsonify({"error": "Wallet not found."}), 500
        coin_account, coinvalue = coin_row
        if float(coins_req) > float(coinvalue):
            return jsonify({"error": f"Insufficient balance. You have {coinvalue} coins."}), 400

        # Fetch user email
        cur.execute("SELECT FULL_NAME, EMAIL, PHONE FROM NAMES WHERE USER_ID=%s LIMIT 1", (user_id,))
        urow = cur.fetchone()
        user_full_name = urow[0] if urow else code_name
        user_email     = urow[1] if urow else ""
        user_phone     = urow[2] if urow else ""
    finally:
        cur.close(); conn.close()

    coins_float   = float(coins_req)
    # Read rate dynamically from DB settings
    try:
        rate_per_coin = float(StoreService.get_setting("coin_to_inr", "10000"))
    except Exception:
        rate_per_coin = 10000.0
    gross_inr     = coins_float * rate_per_coin
    deduction_pct = 10
    deduction_inr = gross_inr * deduction_pct / 100
    net_inr       = gross_inr - deduction_inr

    req_time = datetime.now().strftime("%d %b %Y %I:%M %p IST")
    if upi_id:
        payment_block = (
            f"<tr><td style='padding:5px 0;color:#555;'>Payment Method</td>"
            f"<td align='right' style='padding:5px 0;font-weight:bold;'>UPI Transfer</td></tr>"
            f"<tr><td style='padding:5px 0;color:#555;'>UPI ID</td>"
            f"<td align='right' style='padding:5px 0;'>{upi_id}</td></tr>"
        )
    else:
        payment_block = (
            f"<tr><td style='padding:5px 0;color:#555;'>Payment Method</td>"
            f"<td align='right' style='padding:5px 0;font-weight:bold;'>Bank Transfer (NEFT/IMPS)</td></tr>"
            f"<tr><td style='padding:5px 0;color:#555;'>Account Holder</td>"
            f"<td align='right' style='padding:5px 0;'>{acc_holder}</td></tr>"
            f"<tr><td style='padding:5px 0;color:#555;'>Bank Name</td>"
            f"<td align='right' style='padding:5px 0;'>{bank_name}</td></tr>"
            f"<tr><td style='padding:5px 0;color:#555;'>Account Number</td>"
            f"<td align='right' style='padding:5px 0;'>{account_no}</td></tr>"
            f"<tr><td style='padding:5px 0;color:#555;'>IFSC Code</td>"
            f"<td align='right' style='padding:5px 0;'>{ifsc}</td></tr>"
        )

    admin_html = EmailTemplates.coin_cash_admin(
        code_name, user_full_name, user_email, user_phone,
        coin_account, coins_float, gross_inr, deduction_inr, net_inr,
        payment_block, req_time,
    )
    user_confirm_html = EmailTemplates.coin_cash_user(
        code_name, coins_float, gross_inr, deduction_inr, net_inr
    )

    _send_html_email_async(
        Config.SMTP_USER,
        f"[{Config.COMPANY_NAME}] Coin→Cash Request: {code_name} — {coins_float} Coins / Rs.{net_inr:,.0f}",
        admin_html,
    )
    if user_email:
        _send_html_email_async(
            user_email,
            f"{Config.COMPANY_NAME}: Your Cash Conversion Request (Rs.{net_inr:,.0f})",
            user_confirm_html,
        )

    emit_coin_to_cash(code_name, coins_float)
    return jsonify({
        "ok":          True,
        "coins":       coins_float,
        "gross_inr":   gross_inr,
        "deduction":   deduction_inr,
        "net_inr":     net_inr,
        "message":     f"Request submitted! Rs.{net_inr:,.0f} will be transferred within 2–3 working days."
    })



# =============================================================================
# BROADCAST / NOTIFICATION ROUTES  (DB-backed, delivered to ALL users)
# =============================================================================

@app.get("/api/broadcasts")
def api_broadcasts():
    """Return all broadcasts — any logged-in user can fetch."""
    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(
            "SELECT ID, TITLE, BODY, SENT_BY, CREATED_AT FROM BROADCASTS ORDER BY CREATED_AT DESC LIMIT 50"
        )
        rows = cur.fetchall() or []
        return jsonify({
            "ok": True,
            "broadcasts": [
                {
                    "id":         r[0],
                    "title":      r[1],
                    "body":       r[2],
                    "by":         r[3],
                    "ts":         int(r[4].timestamp() * 1000) if r[4] else 0,
                    "created_at": str(r[4]),
                }
                for r in rows
            ],
        })
    finally:
        cur.close(); conn.close()


@app.post("/api/admin/broadcast")
def api_admin_broadcast():
    """
    Admin sends a broadcast:
    1. Saves to BROADCASTS table (all users pull it on poll)
    2. Sends email to every user who has an email on file
    """
    try:
        _admin_guard()
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), (403 if "admin" in str(exc) else 401)

    payload = request.get_json(force=True) or {}
    title   = (payload.get("title") or "").strip()
    body    = (payload.get("body")  or "").strip()
    send_email_flag = bool(payload.get("send_email", True))

    if not title:
        return jsonify({"error": "Title is required."}), 400
    if not body:
        return jsonify({"error": "Message body is required."}), 400

    _, code_name = _require_login()
    notif_id = f"notif_{int(time.time())}_{uuid.uuid4().hex[:8]}"

    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO BROADCASTS (ID, TITLE, BODY, SENT_BY) VALUES (%s, %s, %s, %s)",
            (notif_id, title, body, code_name),
        )
        conn.commit()

        # Fetch all users with emails for mail delivery
        mail_targets = []
        if send_email_flag and Config.SMTP_USER and Config.SMTP_PASSWORD:
            cur.execute(
                "SELECT CODE_NAME, EMAIL FROM NAMES WHERE EMAIL IS NOT NULL AND EMAIL != '' ORDER BY CREATED_AT"
            )
            mail_targets = [(r[0], r[1]) for r in (cur.fetchall() or [])]

    finally:
        cur.close(); conn.close()

    # Send emails in background
    if mail_targets:
        def _send_broadcast_emails(targets, notif_title, notif_body):
            success_count = 0
            fail_count    = 0
            for cn, email in targets:
                try:
                    html = EmailTemplates.broadcast_notification(cn, notif_title, notif_body)
                    msg            = _MIMEMulti("alternative")
                    msg["Subject"] = f"{Config.COMPANY_NAME}: {notif_title}"
                    msg["From"]    = f"{Config.COMPANY_NAME} <{Config.SMTP_USER}>"
                    msg["To"]      = email
                    msg.attach(_MIMEText(html, "html", "utf-8"))
                    with smtplib.SMTP(Config.SMTP_HOST, Config.SMTP_PORT, timeout=15) as srv:
                        srv.ehlo(); srv.starttls(); srv.ehlo()
                        srv.login(Config.SMTP_USER, Config.SMTP_PASSWORD)
                        srv.sendmail(Config.SMTP_USER, email, msg.as_string())
                    success_count += 1
                except Exception as e:
                    fail_count += 1
                    logging.warning("[Broadcast] Failed to send to %s (%s): %s", cn, email, e)
            logging.info("[Broadcast] Sent %d/%d emails for '%s'",
                         success_count, success_count + fail_count, notif_title)

        threading.Thread(
            target=_send_broadcast_emails,
            args=(mail_targets, title, body),
            daemon=True,
        ).start()

    emit_broadcast(title, body, code_name)
    return jsonify({
        "ok":           True,
        "id":           notif_id,
        "email_queued": len(mail_targets),
        "message":      f"Broadcast saved. Emails queued for {len(mail_targets)} user(s).",
    })


@app.delete("/api/admin/broadcast/<notif_id>")
def api_admin_delete_broadcast(notif_id: str):
    try:
        _admin_guard()
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), (403 if "admin" in str(exc) else 401)

    conn = DatabaseManager.get_connection()
    cur  = conn.cursor()
    try:
        cur.execute("DELETE FROM BROADCASTS WHERE ID=%s", (notif_id,))
        conn.commit()
        return jsonify({"ok": True})
    finally:
        cur.close(); conn.close()


# =============================================================================
# CONTACT / QUERY CONFIRMATION EMAIL
# =============================================================================

@app.post("/api/contact")
def api_contact():
    """
    User submits a contact/query.
    - Sends query details email to admin (SUPPORT_EMAIL)
    - Sends confirmation email to user
    """
    payload = request.get_json(force=True) or {}
    name    = (payload.get("name")    or "").strip()
    email   = (payload.get("email")   or "").strip().lower()
    message = (payload.get("message") or "").strip()

    if not name or not email or not message:
        return jsonify({"error": "Name, email, and message are all required."}), 400
    if "@" not in email:
        return jsonify({"error": "Please enter a valid email address."}), 400

    # Admin notification email
    admin_body = (
        f"<b>New contact query received:</b><br><br>"
        f"<b>Name:</b> {name}<br>"
        f"<b>Email:</b> {email}<br><br>"
        f"<b>Message:</b><br>{message.replace(chr(10), '<br>')}"
    )
    admin_html = EmailTemplates.build(
        title     = "NEW CONTACT QUERY",
        subtitle  = f"FROM {name.upper()}",
        body_html = admin_body,
        footer_note = f"Reply directly to this email to respond to {name}.",
    )

    # User confirmation email
    user_html = EmailTemplates.contact_confirmation(name, message)

    _send_html_email_async(
        Config.SUPPORT_EMAIL,
        f"[{Config.COMPANY_NAME}] New Query from {name}",
        admin_html,
    )
    _send_html_email_async(
        email,
        f"{Config.COMPANY_NAME}: We received your query!",
        user_html,
    )

    emit_contact(name, email)
    return jsonify({"ok": True, "message": "Your query has been received. We'll get back to you soon."})


# =============================================================================
# ENTRY POINT
# =============================================================================

# Initialise DB schema at startup (not on first request) to avoid worker timeouts
with app.app_context():
    try:
        DatabaseManager.ensure_tables_once()
    except Exception as _startup_exc:
        app.logger.error("Startup DB init failed: %s", _startup_exc)


if __name__ == "__main__":
    DatabaseManager.ensure_tables_once()
    app.run(host="0.0.0.0", port=Config.PORT, debug=Config.DEBUG)