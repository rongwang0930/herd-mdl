/*
 * Copyright 2018 herd-mdl contributors
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
**/
package org.tsi.mdlt.test;

import java.io.IOException;
import java.lang.invoke.MethodHandles;
import java.util.HashMap;
import java.util.Map;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.tsi.mdlt.util.shell.ShellHelper;

public class TestSetup {
    private static final Logger LOGGER = LoggerFactory.getLogger(MethodHandles.lookup().lookupClass());

    public static void setUpShepherd(String shepHerdTestFile, Map<String, String> envVars) throws IOException, InterruptedException {
            Map<String, String> setupEnvVars = new HashMap<>(envVars);
            setupEnvVars.put("InputFilesDir", "./mdl/build");
            String shellCommand = "./mdlt/scripts/sh/shellExecutor.sh "
                    + "-w /home/ec2-user "
                    + "-c ./mdlt/scripts/sh/shepherdTestSetup.sh "
                    + "-a " + shepHerdTestFile;
            LOGGER.info(shellCommand);
            ShellHelper.executeShellCommand(shellCommand, setupEnvVars);
    }
}
