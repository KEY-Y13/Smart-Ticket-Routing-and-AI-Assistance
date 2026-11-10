-- REVIEW ONLY; NOT EXECUTED.
-- Proposed target: project-isolated MySQL on 127.0.0.1:33307.
-- CREATE without IF NOT EXISTS deliberately refuses colliding database names.
CREATE DATABASE smartticket_ai CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE smartticket_ai_test CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
