# Databricks notebook source
# MAGIC %md
# MAGIC ### Step 0
# MAGIC Secret Scope, Key Encryption Key Name and User/Group to Access the Key as Inputs 
# MAGIC

# COMMAND ----------

dbutils.widgets.text(name="secret_scope", defaultValue="piiscope", label="The secret scope to use for DEKs")
dbutils.widgets.text(name="kek_name", defaultValue="piikeyname", label="The name to use for our KEK")
dbutils.widgets.text(name="keyvault_user", defaultValue="payroll_managers", label="The username to grant unprivileged access to decrypt the data")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Step 1
# MAGIC Prepare the sample employee and manager tables.

# COMMAND ----------

# MAGIC %sql
# MAGIC --Create a pii_demo catalog & schema
# MAGIC CREATE CATALOG IF NOT EXISTS consume;
# MAGIC CREATE SCHEMA IF NOT EXISTS consume.catalog;
# MAGIC
# MAGIC CREATE OR REPLACE TABLE consume.catalog.employee_hierarchy AS (SELECT * FROM read_files(
# MAGIC   '/Volumes/consume/catalog/synthetic_data/employee_hierarchy.csv',
# MAGIC   format => 'csv',
# MAGIC   header => true,
# MAGIC   inferSchema => true))

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT * FROM consume.catalog.employee_hierarchy

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE consume.catalog.employee_upn AS (SELECT * FROM read_files(
# MAGIC   '/Volumes/consume/catalog/synthetic_data/employee_upn.csv',
# MAGIC   format => 'csv',
# MAGIC   header => true,
# MAGIC   inferSchema => true));

# COMMAND ----------

# MAGIC %sql
# MAGIC select * from consume.catalog.employee_upn;

# COMMAND ----------

# MAGIC %md
# MAGIC ### Step 2
# MAGIC Generate a Key Encryption Key (KEK) and create a key_vault table to store it in. A dedicated catalog and schema are used.

# COMMAND ----------

from base64 import b64encode
from os import urandom

kek = b64encode(urandom(24)).decode('utf-8')

# COMMAND ----------

# MAGIC %sql
# MAGIC --Create a keyvault catalog & schema
# MAGIC CREATE CATALOG IF NOT EXISTS sys;
# MAGIC CREATE SCHEMA IF NOT EXISTS sys.crypto;
# MAGIC
# MAGIC -- Create a table for our keys
# MAGIC CREATE OR REPLACE TABLE sys.crypto.key_vault (
# MAGIC   id BIGINT GENERATED BY DEFAULT AS IDENTITY,
# MAGIC   created_date DATE, 
# MAGIC   created_time TIMESTAMP,
# MAGIC   last_modified_time TIMESTAMP,
# MAGIC   created_by STRING,
# MAGIC   managed_by STRING,
# MAGIC   key_name STRING,
# MAGIC   key_version INT,
# MAGIC   key_enabled BOOLEAN,
# MAGIC   key_type STRING,
# MAGIC   key STRING);
# MAGIC

# COMMAND ----------

kek_name = dbutils.widgets.get("kek_name")

sql(f"""
    INSERT INTO sys.crypto.key_vault (created_date, created_time, last_modified_time, created_by, managed_by, key_name, key_version, key_enabled, key_type, key) 
    VALUES (current_date(), current_timestamp(), current_timestamp(), session_user(), session_user(), '{kek_name}', 1, True, 'KEK', '{kek}')""")

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT * FROM sys.crypto.key_vault

# COMMAND ----------

# MAGIC %md
# MAGIC ### Step 3
# MAGIC Use the KEK to encrypt our Data Encryption Key (DEK) and store the encrypted DEK as a secret (along with Initilisation Vector and Additionally Authenticated Data)

# COMMAND ----------

import string
import random

dek = b64encode(urandom(24)).decode('utf-8')
iv = ''.join(random.choices(string.ascii_uppercase + string.digits, k=12))
aad = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))

encrypted_dek = sql(f"SELECT base64(aes_encrypt('{dek}', '{kek}', 'GCM', 'DEFAULT'))").first()[0]
encrypted_iv = sql(f"SELECT base64(aes_encrypt('{iv}', '{kek}', 'GCM', 'DEFAULT'))").first()[0]
encrypted_aad = sql(f"SELECT base64(aes_encrypt('{aad}', '{kek}', 'GCM', 'DEFAULT'))").first()[0]

# COMMAND ----------

from databricks.sdk import WorkspaceClient

w = WorkspaceClient()

secret_scope = dbutils.widgets.get("secret_scope")

try:
    w.secrets.create_scope(scope=secret_scope)
except Exception as e:
    print(e)

w.secrets.put_secret(scope=secret_scope, key='dek', string_value=encrypted_dek)
w.secrets.put_secret(scope=secret_scope, key='iv', string_value=encrypted_iv)
w.secrets.put_secret(scope=secret_scope, key='aad', string_value=encrypted_aad)

# COMMAND ----------

# grant READ to the users

from databricks.sdk.service import workspace

w.secrets.put_acl(scope=secret_scope, permission=workspace.AclPermission.READ, principal=dbutils.widgets.get("keyvault_user"))

# COMMAND ----------

# MAGIC %md
# MAGIC ### Step 4
# MAGIC Create crypto functions to unwrap our keys and encrypt the data

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE FUNCTION sys.crypto.unwrap_key(key_to_unwrap STRING, key_to_use STRING) 
# MAGIC RETURNS STRING
# MAGIC RETURN aes_decrypt(unbase64(key_to_unwrap), (SELECT key FROM sys.crypto.key_vault WHERE key_enabled AND key_name = key_to_use ORDER BY created_date DESC  LIMIT 1), 'GCM', 'DEFAULT')

# COMMAND ----------

kek_name = dbutils.widgets.get("kek_name")

sql(f"""CREATE OR REPLACE FUNCTION sys.crypto.encrypt(col STRING) 
RETURNS STRING
RETURN 
    base64(aes_encrypt(col, 
    sys.crypto.unwrap_key(secret('{secret_scope}', 'dek'), '{kek_name}'),
    'GCM',  
    'DEFAULT',
    sys.crypto.unwrap_key(secret('{secret_scope}', 'iv'), '{kek_name}'),
    sys.crypto.unwrap_key(secret('{secret_scope}', 'aad'), '{kek_name}')
    ))""")

# COMMAND ----------

# MAGIC %md
# MAGIC ### Step 5
# MAGIC Create a table employee_encrypt with the salary information encrypted

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE consume.catalog.payroll_encrypted AS (SELECT 
# MAGIC employee_id,
# MAGIC first_name,
# MAGIC last_name,
# MAGIC sys.crypto.encrypt(salary) AS salary
# MAGIC FROM consume.catalog.employee_hierarchy)

# COMMAND ----------

# MAGIC %sql
# MAGIC select * from consume.catalog.payroll_encrypted;

# COMMAND ----------

# MAGIC %md
# MAGIC ### Step 6
# MAGIC Create a crypto function to decrypt the data

# COMMAND ----------

sql(f"""CREATE OR REPLACE FUNCTION sys.crypto.decrypt(col STRING) 
RETURNS STRING
RETURN 
    nvl(CAST(try_aes_decrypt(unbase64(col), 
    sys.crypto.unwrap_key(secret('{secret_scope}', 'dek'), '{kek_name}'),
    'GCM',  
    'DEFAULT',
    sys.crypto.unwrap_key(secret('{secret_scope}', 'aad'), '{kek_name}')) AS STRING), 
    col)
    """)

# COMMAND ----------

# MAGIC %md
# MAGIC ### Step 7
# MAGIC Apply the decrypt function to create a view which allows the manager to see their employee data only

# COMMAND ----------

# MAGIC %sql
# MAGIC create or replace view consume.catalog.payroll_decrypted as
# MAGIC select e.employee_id, e.first_name, e.last_name, m.manager_id, m.manager_email, 
# MAGIC sys.crypto.decrypt(e.salary) as salary
# MAGIC from consume.catalog.payroll_encrypted e join consume.catalog.employee_upn m on e.employee_id = m.employee_id
# MAGIC where m.manager_email = current_user()

# COMMAND ----------

# MAGIC %md
# MAGIC ### Step 8
# MAGIC Query the data and confirm that the data is decryped as expected

# COMMAND ----------

# MAGIC %sql
# MAGIC select * from consume.catalog.payroll_decrypted;

# COMMAND ----------

# MAGIC %md
# MAGIC
