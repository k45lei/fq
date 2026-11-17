const assert = require('node:assert/strict');
const { mkdir, readFile } = require('node:fs/promises');
const { join } = require('node:path');

const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright-core');
const origin = 'http://127.0.0.1:4175';
const screenshots = process.env.BROWSER_SCREENSHOTS || '/tmp/futurequest-browser-screenshots';

// Uses the same disposable API fixture as browser-check.cjs. Reference HTML stays untouched.
const run = async () => {
  await mkdir(screenshots, { recursive: true });
  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium',
    headless: true,
    args: process.env.CHROMIUM_NO_SANDBOX === '1' ? ['--no-sandbox'] : [],
  });
  const context = await browser.newContext();
  const page = await context.newPage();
  const reference = await context.newPage();

  try {
    await reference.route('**/__reference/*', async (route) => {
      const name = new URL(route.request().url()).pathname.split('/').pop();
      assert(['landing', 'before_quiz', 'gatherinfo', 'quiz', 'report'].includes(name));
      const body = await readFile(join(__dirname, '../frontend/example_frontend', `${name}.html`), 'utf8');
      await route.fulfill({ contentType: 'text/html', body });
    });

    await page.goto(`${origin}/start`);
    const start = await context.request.post(`${origin}/api/responses`, {
      data: { name: 'Taylor Student', email: 'visual@example.com', school: 'Central Toronto Academy' },
    });
    assert.equal(start.status(), 201);
    const { response_id: responseId, questionnaire } = await start.json();
    const questions = questionnaire.questions;
    await page.evaluate(({ responseId, questionId }) => {
      localStorage.setItem('futurequest.questionnaire', JSON.stringify({
        version: 2,
        state: { responseId, currentQuestionId: questionId, answers: {}, reportId: null },
      }));
    }, { responseId, questionId: questions[7].id });

    const cases = [
      { name: 'landing', path: '/', selectors: ['.navbar-inner', '.hero h1', '.lens-card', '.compass-banner'] },
      { name: 'before_quiz', path: '/before-quiz', selectors: ['.navbar-inner', '.hero h1', '.feature-card', '.how-card'] },
      { name: 'gatherinfo', path: '/start', selectors: ['.navbar-inner', '.step-header h1', '.form-card', 'input[type="email"]'] },
      { name: 'quiz', path: '/questionnaire', selectors: ['.navbar-inner', '.question-card', '.question-text', '.option-row'] },
    ];
    const compare = async (item, width) => {
      await page.setViewportSize({ width, height: 900 });
      await reference.setViewportSize({ width, height: 900 });
      await page.goto(`${origin}${item.path}`);
      await reference.goto(`${origin}/__reference/${item.name}`);
      await page.locator(item.selectors.at(-1)).first().waitFor();
      await Promise.all([page.evaluate(() => document.fonts.ready), reference.evaluate(() => document.fonts.ready)]);
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${item.name} overflows at ${width}px`);

      // These properties should match even when live names, scores, and copy differ.
      for (const selector of item.selectors) {
        const properties = ['fontSize', 'borderRadius', 'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft'];
        const readStyles = (locator) => locator.first().evaluate((element, names) => {
          const style = getComputedStyle(element);
          return Object.fromEntries(names.map((name) => [name, style[name]]));
        }, properties);
        assert.deepEqual(await readStyles(page.locator(selector)), await readStyles(reference.locator(selector)), `${item.name} ${selector} at ${width}px`);
      }
      await page.screenshot({ path: join(screenshots, `${item.name}-${width}-app.png`), fullPage: true });
      await reference.screenshot({ path: join(screenshots, `${item.name}-${width}-reference.png`), fullPage: true });
      console.info(`PASS ${item.name} at ${width}px: reference typography, padding, radii, and no horizontal overflow`);
    };

    for (const item of cases) {
      for (const width of [1280, 390]) {
        await compare(item, width);
      }
    }

    const submitted = await context.request.post(`${origin}/api/responses/${responseId}/sections/interests`, {
      data: {
        answers: questions.map((question) => {
          if (question.answer_type === 'scale') {
            return { question_id: question.id, numeric: question.scale_max };
          }
          return { question_id: question.id, option_ids: question.options.slice(0, question.min_selections ?? 1).map((option) => option.id) };
        }),
      },
    });
    assert.equal(submitted.status(), 200);
    const reportId = (await submitted.json()).report_id;
    for (const width of [1280, 390]) {
      await compare({ name: 'report', path: `/report/${reportId}`, selectors: ['.navbar-inner', '.report-hero h1', '.card', '.career-card', '.upsell-banner'] }, width);
    }
  } finally {
    await browser.close();
  }
};

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
