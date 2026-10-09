// Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

import { getRequestConfig } from 'next-intl/server';
import { cookies } from 'next/headers';
import { resolveLocale } from './locale';

export default getRequestConfig(async () => {
  const cookieStore = cookies();
  const locale = resolveLocale(cookieStore.get('locale')?.value);

  return {
    locale,
    messages: (await import(`../../messages/${locale}.json`)).default,
  };
});
