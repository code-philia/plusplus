import { randomUUID } from 'node:crypto';
import { expect, type Locator, type Page } from '@playwright/test';

type Surface = Page | Locator;

export const AGREEMENT =
  'I have read and agree to abide by Terms of Service and Privacy Policy of 12306.cn.';
export const link = (surface: Surface, name: string) =>
  surface.getByRole('link', { name, exact: true });
export const button = (surface: Surface, name: string) =>
  surface.getByRole('button', { name, exact: true });
export const textbox = (surface: Surface, name: string) =>
  surface.getByRole('textbox', { name, exact: true });
// 原生密码输入框没有隐式 textbox role，按需求声明的标签定位。
export const passwordField = (surface: Surface, name: string) =>
  surface.getByLabel(name, { exact: true });

export async function entry(page: Page) {
  await page.goto('/');
}

export async function openRegistration(page: Page) {
  await link(page, 'Register').click();
  await expect(page.getByRole('combobox', { name: 'Nationality', exact: true })).toBeVisible();
}

export async function openLogin(page: Page) {
  await link(page, 'Login').click();
  await expect(textbox(page, 'Email/Username/Mobile number')).toBeVisible();
}

export function newAccount() {
  // 每次 UI 创建独立身份；用户名及护照号均满足需求的长度和字符限制。
  const identity = randomUUID().replace(/-/g, '').slice(0, 20);
  return {
    username: `smoke-${identity}`,
    email: `smoke-${identity}@example.com`,
    name: `Traveler ${identity}`,
    passport: `P${identity}`,
    password: 'Valid-password-123!',
  };
}

export type Account = ReturnType<typeof newAccount>;

export async function expectSignedIn(page: Page, account: Account) {
  await expect(page.getByText(account.username, { exact: true })).toBeVisible();
  await expect(link(page, 'Sign out')).toBeVisible();
  // 注册和登录都应返回首页，以首页独有的控件确认。
  await expect(textbox(page, 'From')).toBeVisible();
}

export async function registerAccount(page: Page, account: Account) {
  await openRegistration(page);
  const nationality = page.getByRole('combobox', { name: 'Nationality', exact: true });
  // 同时支持原生 select 和提供相同语义的自定义 combobox。
  try {
    await nationality.selectOption({ label: 'Vietnam' }, { timeout: 1_000 });
  } catch {
    await nationality.click();
    await page.getByRole('option', { name: 'Vietnam', exact: true }).click();
  }
  await textbox(page, 'Name').fill(account.name);
  await textbox(page, 'Passport number').fill(account.passport);
  await textbox(page, 'Passport expiration date').fill('2099-12-31');
  await textbox(page, 'Date of birth').fill('1995-06-15');
  await page.getByRole('radiogroup', { name: 'Gender', exact: true })
    .getByRole('radio', { name: 'Male', exact: true }).check();
  await textbox(page, 'Username').fill(account.username);
  await passwordField(page, 'Password').fill(account.password);
  await passwordField(page, 'Confirm Password').fill(account.password);
  await textbox(page, 'Email address').fill(account.email);
  await page.getByRole('checkbox', { name: AGREEMENT, exact: true }).check();
  await button(page, 'Next step').click();
  await expectSignedIn(page, account);
}

export async function signOut(page: Page) {
  await link(page, 'Sign out').click();
  await expect(link(page, 'Register')).toBeVisible();
  await expect(link(page, 'Login')).toBeVisible();
  await expect(link(page, 'Sign out')).toHaveCount(0);
}

export async function signIn(page: Page, identifier: string, password: string) {
  await openLogin(page);
  await textbox(page, 'Email/Username/Mobile number').fill(identifier);
  await passwordField(page, 'Password').fill(password);
  await button(page, 'LOGIN').click();
}
