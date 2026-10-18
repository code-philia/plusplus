// requirement: REQ-1
import { expect, test } from '@playwright/test';
import { button, entry, link, openLogin, openRegistration, passwordField, textbox } from './helpers';


test('REQ-1: 首页显示查询输入框和查询按钮', async ({ page }) => {
  await entry(page);
  for (const name of ['From', 'To', 'Date']) {
    await expect(textbox(page, name)).toBeVisible();
    await expect(textbox(page, name)).toBeEditable();
  }
  await expect(button(page, 'Search')).toBeVisible();
  await expect(button(page, 'Search')).toHaveText('Search');
  await expect(page.getByRole('checkbox', { name: 'High-speed trains only', exact: true })).toBeVisible();
});

test('REQ-1: 点击 Register 打开注册表单', async ({ page }) => {
  await entry(page);
  await openRegistration(page);
  await expect(textbox(page, 'Username')).toBeVisible();
  await expect(button(page, 'Next step')).toBeVisible();
});
