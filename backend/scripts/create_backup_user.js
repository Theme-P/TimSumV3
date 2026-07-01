// Create the least-privilege MongoDB user used by mongodump.
// Safe to run repeatedly. Credentials come from the container environment.

const username = process.env.MONGO_BACKUP_USER;
const password = process.env.MONGO_BACKUP_PASS;

if (!username || !password) {
  print("MongoDB backup user not configured; skipping.");
} else {
  const adminDb = db.getSiblingDB("admin");
  if (adminDb.getUser(username)) {
    print(`MongoDB backup user already exists: ${username}`);
  } else {
    adminDb.createUser({
      user: username,
      pwd: password,
      roles: [{ role: "backup", db: "admin" }]
    });
    print(`MongoDB backup user created: ${username}`);
  }
}
