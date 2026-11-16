const assert = require('node:assert/strict');
const { mkdir } = require('node:fs/promises');
const { join } = require('node:path');

// Optional tooling stays outside application dependencies; see docs/verification.md.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright-core');
const origin = 'http://127.0.0.1:4175';
const screenshots = process.env.BROWSER_SCREENSHOTS || '/tmp/futurequest-browser-screenshots';

const run = async () => {
  await mkdir(screenshots, { recursive: true });
  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM_PATH || '/usr/bin/chromium',
    headless: true,
    args: process.env.CHROMIUM_NO_SANDBOX === '1' ? ['--no-sandbox'] : [],
  });
  const context = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', (error) => errors.push(error.message));

  // Exercise real state/session handling while replacing the external consent redirect.
  await page.route('**/api/auth/google/login', async (route) => {
    const response = await route.fetch({ maxRedirects: 0 });
    const state = new URL(response.headers().location).searchParams.get('state');
    await route.fulfill({
      response,
      headers: {
        ...response.headers(),
        location: `${origin}/api/auth/google/callback?code=browser-test&state=${state}`,
      },
    });
  });

  const visible = async (locator) => locator.waitFor({ state: 'visible' });
  const stored = async () =>
    page.evaluate(() => {
      return JSON.parse(localStorage.getItem('futurequest.questionnaire')).state;
    });

  try {
    await page.goto(origin);
    await visible(page.getByRole('heading', { name: /Find the career/ }));
    await page.screenshot({ path: join(screenshots, 'desktop.png'), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.screenshot({ path: join(screenshots, 'mobile.png'), fullPage: true });

    await page.getByRole('button', { name: 'Use dark theme' }).click();
    assert.equal(await page.locator('html').getAttribute('data-theme'), 'dark');
    await page.reload();
    assert.equal(await page.locator('html').getAttribute('data-theme'), 'dark');
    await page.getByRole('button', { name: 'Use light theme' }).click();
    await page.getByRole('link', { name: 'Start free →', exact: true }).click();
    await page.getByRole('link', { name: 'Start the quiz →', exact: true }).click();
    await page.getByLabel('First name', { exact: true }).fill('Taylor');
    await page.getByLabel('Last name').fill('Student');
    await page.getByLabel('Email', { exact: true }).fill('taylor@example.com');
    await page.getByRole('button', { name: 'Start my quiz →' }).click();
    await page.waitForURL('**/questionnaire');
    const responseId = (await stored()).responseId;

    const completeStage = async (stage) => {
      const response = await context.request.get(
        `${origin}/api/questionnaire?response_id=${responseId}`,
      );
      assert(response.ok());
      const questions = (await response.json()).questions.filter(
        (question) => question.category === stage,
      );

      for (let index = 0; index < questions.length; index += 1) {
        const question = questions[index];
        await visible(page.locator(`section[data-question-id="${question.id}"]`));
        const buttons = page.locator('section button[aria-pressed]');

        if (question.answer_type === 'multi_choice') {
          for (let choice = 0; choice < question.min_selections; choice += 1) {
            await buttons.nth(choice).click();
          }

          if (index < questions.length - 1) {
            await page.getByRole('button', { name: 'Next', exact: true }).click();
          }
        } else {
          await buttons.nth(question.answer_type === 'scale' ? 2 : 0).click();
        }

        if (stage === 'interests' && index === 3) {
          const next = page.locator(`section[data-question-id="${questions[index + 1].id}"]`);
          await visible(next);
          await page.reload();
          await visible(next);
          assert.equal(Object.keys((await stored()).answers).length, 4);
        }
      }

      await page.getByRole('button', { name: 'See my results', exact: true }).click();
      await page.waitForURL('**/report/*');
      await visible(page.getByRole('heading', { name: 'Your top career matches' }));
      console.info(`PASS ${stage}: all questions completed and report displayed`);
    };

    await completeStage('interests');
    await visible(page.locator('svg[role="img"]'));
    assert.equal(await page.locator('ol > li').count(), 8);
    await page.screenshot({ path: join(screenshots, 'report-mobile.png'), fullPage: true });
    await page.reload();
    await visible(page.getByRole('heading', { name: 'Your top career matches' }));

    await page.getByRole('button', { name: /Unlock the remaining assessments/ }).click();
    await page.getByRole('link', { name: 'Cancel', exact: true }).click();
    await visible(page.getByRole('heading', { name: 'No charge was made' }));
    await page.getByRole('link', { name: 'Back to the questions' }).click();
    await visible(page.getByRole('heading', { name: 'Your top career matches' }));
    await page.getByRole('button', { name: /Unlock the remaining assessments/ }).click();
    await page.getByRole('link', { name: 'Pay', exact: true }).click();
    await page.waitForURL('**/questionnaire');

    await completeStage('strengths');
    await visible(page.getByRole('heading', { name: 'What motivates you' }));
    await page.getByRole('link', { name: /Continue to Emotional intelligence/ }).click();
    await completeStage('eq');
    await visible(page.getByRole('heading', { name: 'Your combined Compass' }));
    assert.equal(await page.locator('ol > li').count(), 5);
    assert.equal(
      await page.getByRole('button', { name: /Unlock the remaining assessments/ }).count(),
      0,
    );
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.screenshot({ path: join(screenshots, 'compass.png'), fullPage: true });

    await page.getByRole('link', { name: 'Sign in', exact: true }).first().click();
    await page.waitForURL(`${origin}/`);
    await page.goto(`${origin}/reports`);
    await visible(page.getByText('Signed in as browser@example.com.', { exact: false }));
    assert.equal(await page.getByRole('link', { name: 'Open', exact: true }).count(), 3);
    await page.getByRole('button', { name: 'Sign out', exact: true }).click();
    await page.waitForURL(`${origin}/`);
    assert.equal((await stored()).responseId, null);
    await page.goto(`${origin}/reports`);
    await visible(page.getByRole('link', { name: 'Sign in with Google' }));
    assert.deepEqual(errors, []);
    console.info(
      'PASS responsive layout, theme, draft refresh, checkout cancellation/payment, Compass, mocked Google claim, history, and logout; no browser exceptions',
    );
  } catch (error) {
    await page.screenshot({ path: join(screenshots, 'failure.png'), fullPage: true });
    console.error('Failed at', page.url());
    throw error;
  } finally {
    await browser.close();
  }
};

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
