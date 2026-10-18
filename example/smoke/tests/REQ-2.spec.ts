// requirement: REQ-2
import { expect, test } from '@playwright/test';
import { AGREEMENT, button, entry, expectSignedIn, newAccount, openRegistration, passwordField, registerAccount, textbox } from './helpers';

test('REQ-2: 注册页显示旅客身份字段和性别选项', async ({ page }) => {
  await entry(page);
  await openRegistration(page);
  for (const name of ['Name', 'Passport number', 'Passport expiration date', 'Date of birth']) {
    await expect(textbox(page, name)).toBeVisible();
  }
  const gender = page.getByRole('radiogroup', { name: 'Gender', exact: true });
  await expect(gender).toBeVisible();
  for (const name of ['Male', 'Female']) {
    await expect(gender.getByRole('radio', { name, exact: true })).toBeVisible();
  }
});

test('REQ-2: 注册页显示凭据字段、协议和提交按钮', async ({ page }) => {
  await entry(page);
  await openRegistration(page);
  for (const name of ['Username', 'Email address']) {
    await expect(textbox(page, name)).toBeVisible();
  }
  for (const name of ['Password', 'Confirm Password']) {
    await expect(passwordField(page, name)).toBeVisible();
    await expect(passwordField(page, name)).toHaveAttribute('type', 'password');
  }
  await expect(page.getByRole('checkbox', { name: AGREEMENT, exact: true })).toBeVisible();
  await expect(button(page, 'Next step')).toBeVisible();
  await expect(button(page, 'Next step')).toHaveText('Next step');
});

test('REQ-2: 有效注册后自动登录且刷新保持会话', async ({ page }) => {
  await entry(page);
  const account = newAccount();
  await registerAccount(page, account);
  await page.reload();
  await expectSignedIn(page, account);
});
