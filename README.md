# Securing-Sensitive-Data
![ssd](https://github.com/user-attachments/assets/9eb9e95f-9b24-471c-8680-57c28c6b28df)

Securing Sensitive Data-DEMO

Step 0 - Secret Scope, Key Encryption Key Name and User/Group to Access the Key as Inputs

Step 1 - Prepare the sample employee and manager tables

Step 2 - Generate a Key Encryption Key (KEK) and create a key_vault table to store it in a dedicated catalog and schema

Step 3 - Use the KEK to encrypt our Data Encryption Key (DEK) and store the encrypted DEK as a secret 

Step 4 - Create crypto functions to unwrap our keys and encrypt the data

Step 5 - Create a table employee_encrypt with the salary information encrypted

Step 6 - Create a crypto function to decrypt the data

Step 7 - Apply the decrypt function to create a view which allows the manager to see their employee data only

Step 8 - Query the data and confirm that the data is decrypted as expected
