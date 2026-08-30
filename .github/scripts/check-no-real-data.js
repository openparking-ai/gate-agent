#!/usr/bin/env node
/**
 * No real personal data in the repository.
 *
 * The running system stores real vehicle identity — that is the product. What
 * must never appear in the REPOSITORY is real data: fixtures, tests, docs and
 * examples all use invented values.
 *
 * This is the repo-side half of that rule. The metadata half is
 * check-commit-emails.js.
 *
 * AND SINCE THIS PACKAGE STORES IMAGES, IT REFUSES AN IMAGE FILE OUTRIGHT.
 * Not "an image of a plate", not "an image of a car": ANY image, anywhere in
 * the tree. A photograph committed as a fixture is the exact thing this project
 * has decided a store keeps for thirty days and then deletes, and a rule that
 * asked what was IN a picture would be a rule nobody can run. Every image in
 * this repository's tests is synthetic and is built in the process that uses it
 * (`tests/cameras.py`), so there is nothing here for this to refuse.
 *
 * Usage:
 *   check-no-real-data.js               scan every tracked file
 *   check-no-real-data.js --self-test   prove the scan can fail
 */
import { execFileSync } from 'node:child_process';
import { readFileSync, writeFileSync, rmSync } from 'node:fs';

const EMAIL = /\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b/g;

/** Addresses that are fine to write down. */
const ALLOWED_EMAIL = [
  /@users\.noreply\.github\.com$/i,
  /^noreply@github\.com$/i,
  /^noreply@anthropic\.com$/i,
  /@example\.(com|org|net)$/i,
  /^[^@]+@example$/i,
];

/** Things that are real, and are named so the scan cannot miss them. */
const FORBIDDEN = [
  { pattern: /gulec@me\.com/i, why: "a maintainer's personal address" },
  { pattern: /gokhan@72knots\.ai/i, why: "a maintainer's work address" },
];

const SKIP = /^(LICENSE|package-lock\.json|\.github\/scripts\/check-no-real-data\.js)$/;

/** Image extensions, refused by name. */
const IMAGE_EXTENSION = /\.(jpe?g|png|gif|bmp|webp|tiff?|heic|heif|avif|ico|svg)$/i;

/**
 * What an image file starts with, refused by CONTENT.
 *
 * Both halves are needed and neither is enough. A `.jpg` that is empty is still
 * a file somebody meant to be an image; a JPEG committed as `frame.dat` is
 * still a JPEG. Keying on one of them is a check somebody walks past by
 * renaming a file.
 */
const IMAGE_MAGIC = [
  { bytes: [0xff, 0xd8, 0xff], what: 'JPEG' },
  { bytes: [0x89, 0x50, 0x4e, 0x47], what: 'PNG' },
  { bytes: [0x47, 0x49, 0x46, 0x38], what: 'GIF' },
  { bytes: [0x42, 0x4d], what: 'BMP' },
];

function imageProblems(file, buffer) {
  const problems = [];
  if (IMAGE_EXTENSION.test(file)) {
    problems.push({ file, value: file, why: 'an image file; every image here is synthetic and built in the test that uses it' });
  }
  for (const { bytes, what } of IMAGE_MAGIC) {
    if (buffer.length >= bytes.length && bytes.every((b, i) => buffer[i] === b)) {
      problems.push({ file, value: what, why: `${what} data in a tracked file` });
      break;
    }
  }
  return problems;
}

function trackedFiles() {
  return execFileSync('git', ['ls-files'], { encoding: 'utf8' })
    .split('\n')
    .filter(Boolean)
    .filter((f) => !SKIP.test(f));
}

function scanText(file, text) {
  const problems = [];
  for (const { pattern, why } of FORBIDDEN) {
    if (pattern.test(text)) problems.push({ file, value: pattern.source, why });
  }
  for (const match of text.match(EMAIL) ?? []) {
    if (!ALLOWED_EMAIL.some((re) => re.test(match))) {
      problems.push({ file, value: match, why: 'an email address that is not obviously invented' });
    }
  }
  return problems;
}

function scanRepo() {
  const problems = [];
  for (const file of trackedFiles()) {
    let buffer;
    try {
      buffer = readFileSync(file);
    } catch {
      continue; // unreadable
    }
    problems.push(...imageProblems(file, buffer));
    problems.push(...scanText(file, buffer.toString('utf8')));
  }
  return problems;
}

function selfTest() {
  const probe = '_no_real_data_control.md';
  try {
    writeFileSync(probe, 'contact someone.real@a-real-company.example-not\n');
    const caught = scanText(probe, readFileSync(probe, 'utf8'));
    if (caught.length === 0) {
      console.error('SELF-TEST FAILED: a planted address was not caught');
      return false;
    }
    const clean = scanText(probe, 'write to nobody@example.com, which is invented\n');
    if (clean.length !== 0) {
      console.error('SELF-TEST FAILED: an example.com address was wrongly rejected');
      return false;
    }
    console.log('self-test OK — a real-looking address fails; an example.com one passes.');
  } finally {
    rmSync(probe, { force: true });
  }

  // AND THE IMAGE HALF, both ways round: by name and by content.
  const planted = '_no_real_data_control.jpg';
  const renamed = '_no_real_data_control.dat';
  try {
    writeFileSync(planted, Buffer.from([0xff, 0xd8, 0xff, 0xe0, 0x00, 0x10]));
    if (imageProblems(planted, readFileSync(planted)).length === 0) {
      console.error('SELF-TEST FAILED: a planted .jpg was not caught');
      return false;
    }
    writeFileSync(renamed, Buffer.from([0xff, 0xd8, 0xff, 0xe0, 0x00, 0x10]));
    if (imageProblems(renamed, readFileSync(renamed)).length === 0) {
      console.error('SELF-TEST FAILED: JPEG data under a non-image name was not caught');
      return false;
    }
    if (imageProblems('docs/CONTRACT.md', Buffer.from('# a document\n')).length !== 0) {
      console.error('SELF-TEST FAILED: an ordinary text file was wrongly rejected');
      return false;
    }
    console.log('self-test OK — a .jpg fails, JPEG bytes under any name fail, a document passes.');
    return true;
  } finally {
    rmSync(planted, { force: true });
    rmSync(renamed, { force: true });
  }
}

if (process.argv[2] === '--self-test') process.exit(selfTest() ? 0 : 1);

const problems = scanRepo();
if (problems.length > 0) {
  console.error('\nREAL DATA IN THE REPOSITORY\n');
  for (const p of problems) console.error(`  ${p.file}: ${p.value}  (${p.why})`);
  console.error('\nFixtures, tests and docs use invented values.\n');
  process.exit(1);
}
console.log(
  `${trackedFiles().length} tracked file(s) scanned; no real personal data and no image.`
);
