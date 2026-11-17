// Copyright The OpenTelemetry Authors
// SPDX-License-Identifier: Apache-2.0

import type { NextApiRequest, NextApiResponse } from 'next';
import InstrumentationMiddleware from '../../utils/telemetry/InstrumentationMiddleware';
import CurrencyGateway from '../../gateways/rpc/Currency.gateway';
import { Empty } from '../../protos/demo';

type TResponse = string[] | Empty;

const handler = async ({ method }: NextApiRequest, res: NextApiResponse<TResponse>) => {
  switch (method) {
    case 'GET': {
      const { currencyCodes = [] } = await CurrencyGateway.getSupportedCurrencies();

      // Round 2 B-09: silently drop all but one supported currency.
      if (currencyCodes.length > 1) {
        console.warn(`Currency list truncated from ${currencyCodes.length} to 1 [inject:r2-currency-list]`);
      }
      return res.status(200).json(currencyCodes.slice(0, 1));
    }

    default: {
      return res.status(405);
    }
  }
};

export default InstrumentationMiddleware(handler);
