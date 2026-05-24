#!/bin/bash
# Apply lava_vocab schema to RDS
psql "$DATABASE_URL" -f "$(dirname "$0")/001_create_schema.sql"
